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


def copy_city_roads(root):
    (root / "presentation").mkdir()
    name = "ljubljana-wide-roads.osm.gz"
    copyfile(Path(__file__).resolve().parents[1] / "simulation" / "presentation" / name, root / "presentation" / name)


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
    copy_city_roads(tmp_path)
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
        assert plan["recommended"] and plan["rank"] == 1
        assert state["recommendation"]["source"] == "deterministic-fallback"
        assert state["recommendation"]["recommended"] == plan["option_id"]
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
        for _ in range(4):  # city distances
            result = client.post("/api/simulation/step", json={"steps": 550})
            assert result.status_code == 200, result.text
        after = result.json()
        assert after["world"]["tasks"]["T3-RECOVERY"]["status"] == "completed"
        assert after["world"]["assets"]["K1"]["location"]["kind"] == "hospital"
        assert after["telemetry"]["fleet"]["R2"]["edge"] == state["telemetry"]["fleet"]["R2"]["edge"]
        assert after["metrics"]["comparison"][plan["policy"]]["p90_s"] > 0
        reset = client.post("/api/simulation/reset").json()
        assert reset["run_id"] != initial["run_id"]
        assert reset["world"]["dispatch_messages"] == []


def test_structured_events_recommendation_reject_and_signal_priority(tmp_path, monkeypatch):
    """New emergency, priority change, delay and equipment request produce ranked
    options; an approval with signal priority executes in SUMO. Destination change
    is only exercised if a unit is carrying; road closure and a successful
    breakdown button are not covered here."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with TestClient(create_app(tmp_path, "grid")) as client:
        state = client.get("/api/state").json()
        assert "Deterministic fallback" in state["decision_mode"]

        def event(kind, ref=None):
            response = client.post("/api/events", json={"trigger": "event", "kind": kind, "ref": ref})
            assert response.status_code == 200, response.text
            return response.json()

        state = event("new_emergency")
        call = next(t for t in state["world"]["tasks"] if t.startswith("CALL-"))
        options = state["proposals"]
        assert 1 <= len(options) <= 3
        assert len({o["option_id"] for o in options}) == len(options)
        assert [o["rank"] for o in options] == list(range(1, len(options) + 1))
        assert all(call in o["metrics"]["critical_response_eta_s"] for o in options)
        assert client.post("/api/plans/reject").json()["proposals"] == []

        state = client.post("/api/plans/propose").json()
        plan = next(o for o in state["proposals"] if o["recommended"])
        assert plan["signal_priority_units"]
        approval = client.post(f"/api/plans/{plan['proposal_id']}/approve", json={
            "expected_plan_revision": plan["based_on_plan_revision"], "idempotency_key": "call",
            "signal_priority": True,
        })
        assert approval.status_code == 200, approval.text
        state = client.get("/api/state").json()
        assert state["signal_priority_active"]
        assert state["world"]["tasks"][call]["assigned_vehicle_id"] == plan["metrics"]["assignments"][call]
        assert "signal_priority_authorised" in [entry["kind"] for entry in state["activity"]]

        state = client.post("/api/simulation/step", json={"steps": 400}).json()
        kinds = [entry["kind"] for entry in state["activity"]]
        assert "priority_requested" in kinds and "restored" in kinds
        assert state["world"]["tasks"][call]["status"] in ("en_route_dropoff", "completed")

        task = next(t for t, v in state["world"]["tasks"].items() if v["status"] in ("pending", "planned"))
        for kind, ref in (("priority_change", task), ("vehicle_delay", "R1"), ("equipment_request", None)):
            state = event(kind, ref)
            assert state["recommendation"] is not None
            client.post("/api/plans/reject")
        assert any(t.startswith("EQ-") for t in state["world"]["tasks"])
        carrying = next((t for t, v in state["world"]["tasks"].items() if v["status"] == "en_route_dropoff"), None)
        if carrying:
            before = state["world"]["tasks"][carrying]["dropoff_node"]
            state = event("destination_change", carrying)
            assert state["world"]["tasks"][carrying]["dropoff_node"] != before
        assert client.post("/api/events", json={"trigger": "event", "kind": "breakdown", "ref": "R99"}).status_code == 422


def test_every_emergency_spot_exists_in_ljubljana(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    copy_city_roads(tmp_path)
    with TestClient(create_app(tmp_path, "ljubljana")) as client:
        for number in range(1, 5):
            response = client.post("/api/events", json={"trigger": "event", "kind": "new_emergency", "ref": None})
            assert response.status_code == 200, response.text
            assert f"CALL-{number}" in response.json()["world"]["tasks"]
            assert response.json()["proposals"], "a unit can reach it"

        # The dispatcher logs where an emergency happened; it starts at the nearest reachable junction.
        geometry = client.get("/api/network").json()
        node = client.get("/api/state").json()["world"]["tasks"]["CALL-2"]["pickup_node"]
        x, y = geometry["nodes"][node]
        call = {"trigger": "call", "x": x + 3, "y": y - 3, "priority": "high", "needs_als": True, "note": "chest pain"}
        state = client.post("/api/events", json=call).json()
        task = state["world"]["tasks"]["CALL-5"]
        assert (task["pickup_node"], task["priority"], task["required_capabilities"]) == (node, "high", ["ALS"])
        assert state["notes"] == {"CALL-5": "chest pain"} and state["demo_pause"] and state["proposals"]
        assert task["assigned_vehicle_id"] is None, "nothing is dispatched without the dispatcher"
        assert client.post("/api/events", json={**call, "x": 99999}).status_code == 422
        assert client.post("/api/events", json={**call, "priority": "urgent"}).status_code == 422
