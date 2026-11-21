"""High-value API smoke checks through the real SUMO owner."""

from pathlib import Path
from shutil import copyfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("dotenv")

from fastapi.testclient import TestClient

from rescueflow.dashboard.app import create_app
from rescueflow.sumo.env import resolve_sumo_env

pytestmark = pytest.mark.skipif(resolve_sumo_env() is None, reason="SUMO is required for the dashboard")


def test_interpretation_uses_world_and_never_freezes_on_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with TestClient(create_app(tmp_path, "grid")) as client:
        state = client.get("/api/state").json()
        assert state["agent_mode"] == "Rule-based fallback — AI unavailable"
        for text, error in [
            ("Vozilo R99 je obstalo.", "unknown_vehicle"),
            ("Vozilo R1 je obstalo. Komplet K1 je ostal v vozilu.", "asset_location_conflict"),
            ("Vozilo R2 je obstalo, cesta je zaprta.", "needs_confirmation"),
        ]:
            result = client.post("/api/events/interpret", json={"message": text}).json()
            assert not result["valid"]
            assert result["code"] == error
        assert client.get("/api/state").json() == state
        assert client.get("/api/notifications").json() == []


def test_ljubljana_report_plan_approval_and_live_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    (tmp_path / "scenario").mkdir()
    copyfile(
        Path(__file__).resolve().parents[1] / "simulation" / "scenario" / "ljubljana-roads.osm.gz",
        tmp_path / "scenario" / "ljubljana-roads.osm.gz",
    )
    with TestClient(create_app(tmp_path, "ljubljana")) as client:
        initial = client.get("/api/state").json()
        geometry = client.get("/api/network").json()
        assert initial["network"] == geometry["network"] == "ljubljana"
        assert geometry["signals"] and geometry["edges"]
        start = initial["world"]["sim_time_s"]
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json()["sequence"] == initial["sequence"]
        report = client.post("/api/events/interpret", json={
            "message": "Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu."
        }).json()
        assert report["valid"] and report["draft"]["vehicle_id"] == "R2"
        state = client.post("/api/events", json={"trigger": "report", "report_id": report["report_id"]}).json()
        assert state["demo_pause"]
        assert state["world"]["vehicles"]["R2"]["status"] == "broken"
        assert state["world"]["assets"]["K1"]["location"]["kind"] == "roadside"
        assert len(state["proposals"]) >= 2
        plan = state["proposals"][0]
        assert all(
            edge in geometry["edges"]
            for route in plan["routes"].values() for edge in route
        )
        recovery = next(assignment for assignment in plan["assignments"] if assignment["task_id"] == "T3-RECOVERY")
        assert recovery["pickup_s"] >= start
        url = f"/api/plans/{plan['proposal_id']}/approve"
        request = {"expected_plan_revision": plan["based_on_plan_revision"], "idempotency_key": "first"}
        assert client.post(url, json={**request, "expected_plan_revision": 0}).status_code == 409
        approval = client.post(url, json=request)
        assert approval.status_code == 200, approval.text
        assert client.post(url, json=request).json() == approval.json()
        assert client.get("/api/notifications").json() == approval.json()["dispatch_messages"]
        assert any(message["task_id"] == "T3-RECOVERY" for message in approval.json()["dispatch_messages"])
        for _ in range(2):
            result = client.post("/api/simulation/step", json={"steps": 550})
            assert result.status_code == 200, result.text
        after = result.json()
        assert after["world"]["tasks"]["T3-RECOVERY"]["status"] == "completed"
        assert after["world"]["assets"]["K1"]["location"]["kind"] == "hospital"
        assert after["telemetry"]["fleet"]["R2"]["edge"] == initial["telemetry"]["fleet"]["R2"]["edge"]
        assert after["metrics"]["comparison"]["a1"]["p90_s"] > 0
        reset = client.post("/api/simulation/reset").json()
        assert reset["run_id"] != initial["run_id"]
        assert reset["world"]["dispatch_messages"] == []
