"""Event -> options -> approval -> SUMO, through the real dashboard API and the
real benchmark loop. No test here calls the AI: the dashboard tests use the
labeled fallback, and the benchmark test uses a stand-in Decisions client."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from rescueflow.benchmark.montecarlo import Run
from rescueflow.benchmark.scenarios import prepare
from rescueflow.dashboard.app import create_app
from rescueflow.decision.agent import SOURCE_AI, DecisionAgent
from rescueflow.sumo.env import resolve_sumo_env

pytestmark = pytest.mark.skipif(resolve_sumo_env() is None, reason="SUMO is required")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with TestClient(create_app(tmp_path, "grid")) as opened:
        yield opened


def post(client, path, body=None):
    response = client.post(path, json=body or {})
    assert response.status_code == 200, response.text
    return response.json()


def event(client, kind, ref=None):
    return post(client, "/api/events", {"trigger": "event", "kind": kind, "ref": ref})


def approve(client, state, choose=lambda option: True):
    plan = next(o for o in state["proposals"] if choose(o))
    post(client, f"/api/plans/{plan['proposal_id']}/approve", {
        "expected_plan_revision": plan["based_on_plan_revision"], "idempotency_key": plan["proposal_id"]})
    return plan


def step(client, seconds):
    return post(client, "/api/simulation/step", {"steps": seconds})


def clear_of_junction(client, unit):
    for _ in range(30):
        state = step(client, 1)
        if not state["telemetry"]["fleet"][unit]["edge"].startswith(":"):
            return state
    raise AssertionError(f"{unit} never left a junction")


def test_breakdown_button_reassigns_work_and_the_unit_stays_put(client):
    plan = approve(client, post(client, "/api/plans/propose"))
    # T6 needs the only ALS crew, so break a unit whose work someone else can do.
    als = plan["metrics"]["assignments"]["T6"]
    unit, task = next((v, t) for t, v in plan["metrics"]["assignments"].items() if t in ("T1", "T4", "T7") and v != als)
    state = clear_of_junction(client, unit)
    assert state["world"]["tasks"][task]["status"] in ("planned", "en_route_pickup")
    state = event(client, "breakdown", unit)
    assert state["world"]["vehicles"][unit]["status"] == "broken"
    place = state["telemetry"]["fleet"][unit]["edge"]
    moved = [t for t, v in plan["metrics"]["assignments"].items() if v == unit]
    replacement = approve(client, state)
    taken = {t: v for t, v in replacement["metrics"]["assignments"].items() if t in moved}
    assert taken and unit not in taken.values(), "other units take the broken unit's work"
    for _ in range(8):
        state = step(client, 500)
        if all(state["world"]["tasks"][other]["status"] == "completed" for other in taken):
            break
    for other in taken:
        assert state["world"]["tasks"][other]["status"] == "completed"
    assert state["telemetry"]["fleet"][unit]["edge"] == place


def test_destination_change_sends_the_carrying_unit_to_the_new_hospital(client):
    before = client.get("/api/state").json()["world"]["tasks"]["T3"]
    assert before["status"] == "en_route_dropoff" and before["assigned_vehicle_id"] == "R2"
    clear_of_junction(client, "R2")
    state = event(client, "destination_change", "T3")
    target = state["world"]["tasks"]["T3"]["dropoff_node"]
    assert target != before["dropoff_node"]
    if state["proposals"]:
        post(client, "/api/plans/reject")
    for _ in range(3):
        state = step(client, 500)
    assert state["world"]["tasks"]["T3"]["status"] == "completed"
    assert state["world"]["assets"]["K1"]["location"] == {"kind": "hospital", "ref_id": target}


def test_road_closure_reroutes_around_the_closed_road(client):
    state = clear_of_junction(client, "R2")
    route = state["telemetry"]["fleet"]["R2"]["route"]
    closed = route[2]
    state = post(client, "/api/events", {"trigger": "road_closure", "edge_id": closed, "duration_s": 1200})
    assert closed in state["world"]["closed_edges"]
    approve(client, state)
    visited = set()
    for _ in range(240):
        state = step(client, 5)
        seen = state["telemetry"]["fleet"]["R2"]
        visited.add(seen["edge"])
        assert closed not in seen["route"], "R2 still plans to use the closed road"
        if state["world"]["tasks"]["T3"]["status"] == "completed":
            break
    assert closed not in visited
    assert state["world"]["tasks"]["T3"]["status"] == "completed"


def test_delayed_unit_stands_still_for_the_reported_time(client):
    clear_of_junction(client, "R2")
    event(client, "vehicle_delay", "R2")
    post(client, "/api/plans/reject") if client.get("/api/state").json()["proposals"] else None
    start = step(client, 15)["telemetry"]["fleet"]["R2"]
    held = step(client, 90)["telemetry"]["fleet"]["R2"]
    assert start["xy"] == held["xy"]
    later = step(client, 60)["telemetry"]["fleet"]["R2"]
    assert later["xy"] != held["xy"]


def test_priority_change_reorders_options(client):
    state = event(client, "priority_change", "T1")
    assert state["world"]["tasks"]["T1"]["priority"] == "critical"
    assert all("T1" in o["metrics"]["critical_response_eta_s"] for o in state["proposals"])
    plan = approve(client, state)
    state = client.get("/api/state").json()
    assert state["world"]["tasks"]["T1"]["assigned_vehicle_id"] == plan["metrics"]["assignments"]["T1"]


def test_benchmark_mode_executes_the_ai_choice_and_reports_observed_times(tmp_path):
    picks = []

    def create(**request):
        values = [c["value"] for c in request["questions"][0]["choices"]]
        picks.append(values[-1])
        answer = SimpleNamespace(type="choice", choice=values[-1], probabilities=[
            SimpleNamespace(value=v, probability=0.8 if v == values[-1] else 0.1) for v in values])
        return SimpleNamespace(answers=[answer], model="stand-in", usage=None)

    agent = DecisionAgent(enabled=False)
    agent.client = SimpleNamespace(decisions=SimpleNamespace(create=create))
    run = Run(prepare(tmp_path, 3), tmp_path, "A2", agent)
    try:
        run.execute()
    finally:
        run.controller.close()
    ranked = [d for d in run.decisions if d["source"] == SOURCE_AI]
    assert ranked and [d["option_id"] for d in ranked] == picks
    assert sum(e["kind"] == "plan_committed" for e in run.events) >= len(ranked)

    failures = []
    metrics = run.metrics(tmp_path, failures)
    assert not failures and metrics["valid"]
    reported = {e["event"]["payload"]["task_id"]: e["sim_time_s"] for e in run.events
                if e["kind"] == "event_applied" and e["event"]["type"] == "new_urgent_task"}
    for task_id, call in metrics["calls"].items():
        arrivals = [e["sim_time_s"] for e in run.controller.event_log if e["kind"] == "stop_begin"
                    and e.get("task_id") == task_id and e["stop_kind"] == "pickup"]
        assert call["response_s"] == (arrivals[0] - reported[task_id] if arrivals else None)
    json.dumps(metrics)


def test_freed_unit_makes_the_dashboard_offer_options_again(client):
    plan = approve(client, post(client, "/api/plans/propose"))
    runtime = client.app.state.runtime
    runtime._queued = {"T1"}
    runtime._trigger = SimpleNamespace(fires=lambda world, decided: decided(SimpleNamespace(id="T1")), mark=lambda world: None)
    state = step(client, 1)
    assert state["proposals"] and state["demo_pause"], "the dispatcher decides; nothing is committed"
    assert state["world"]["tasks"]["T1"]["assigned_vehicle_id"] == plan["metrics"]["assignments"]["T1"]
    runtime._trigger.fires = lambda world, decided: False
    post(client, "/api/plans/reject")
    assert step(client, 1)["proposals"] == []
