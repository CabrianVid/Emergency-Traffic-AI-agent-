"""Real SUMO execution-loop tests (prompt 2): physical movement, dwell
completion, asset custody transfer, broken-vehicle freeze, and live road
closure. Skipped (with a reason) if SUMO/the network isn't available --
never asserts success without an actual simulation having run.

Each test builds a small, fast, standalone WorldState (short hops) rather
than the full 7-task demo scenario, so this suite stays quick."""

from __future__ import annotations

from pathlib import Path

import pytest

NET_PATH = Path("simulation/network/city.net.xml")
CFG_PATH = Path("simulation/network/city.sumocfg")


def _sumo_available() -> bool:
    from rescueflow.sumo.env import resolve_sumo_env

    return resolve_sumo_env() is not None


pytestmark = [
    pytest.mark.skipif(not _sumo_available(), reason="SUMO not found on this machine"),
    pytest.mark.skipif(not CFG_PATH.exists(), reason="simulation network not generated; run 'rescueflow sumo setup'"),
]


def _one_task_world(tmp_path, pickup="A1", dropoff="A3", vehicle_node="A0"):
    from rescueflow.models.core import (
        Asset,
        AssetLocation,
        Task,
        TaskKind,
        TaskPriority,
        Vehicle,
        VehicleMode,
        WorldState,
    )
    from rescueflow.scenario.loader import _two_stop_route
    from rescueflow.travel import SumoTravelTimeProvider

    travel = SumoTravelTimeProvider(str(NET_PATH))
    task = Task(
        id="T1", kind=TaskKind.equipment_delivery, priority=TaskPriority.high,
        pickup_node=pickup, dropoff_node=dropoff, created_at_s=0.0, equipment_units=1,
        required_asset_ids=["K1"], load_seconds=10.0, unload_seconds=10.0,
    )
    vehicle = Vehicle(id="V1", mode=VehicleMode.ambulance, node_id=vehicle_node, equipment_capacity_units=1)
    vehicle.route_stops, _ = _two_stop_route(vehicle, task, travel, start_time_s=0.0)
    world = WorldState(
        vehicles={"V1": vehicle}, tasks={"T1": task},
        assets={"K1": Asset(id="K1", location=AssetLocation(kind="depot", ref_id=pickup), status="available")},
    )
    return world


def _controller(tmp_path):
    from rescueflow.sumo.controller import TraciSimulationController

    return TraciSimulationController(str(CFG_PATH), str(NET_PATH), state_path=str(tmp_path / "state.xml.gz"))


def test_pickup_and_dropoff_complete_and_transfer_custody(tmp_path):
    world = _one_task_world(tmp_path)
    controller = _controller(tmp_path)
    controller.load_scenario(world)

    for _ in range(40):
        controller.connect(resume=True, world=world)
        controller.step(world, n=30)
        controller.save_and_close()
        if world.tasks["T1"].status.value == "completed":
            break

    assert world.tasks["T1"].status.value == "completed", world.tasks["T1"].status
    stops = world.vehicles["V1"].route_stops
    assert all(s.service_completed_at_s is not None for s in stops)
    assert world.assets["K1"].location.kind in ("hospital", "depot")
    assert world.assets["K1"].location.ref_id == stops[-1].node_id
    assert world.assets["K1"].status == "available"
    assert "K1" not in world.vehicles["V1"].equipment_aboard


def test_broken_vehicle_never_moves_again(tmp_path):
    from rescueflow.events import RuleBasedEventInterpreter, apply_event

    world = _one_task_world(tmp_path, pickup="A3", dropoff="A6")  # longer leg, more time to intercept mid-flight
    controller = _controller(tmp_path)
    controller.load_scenario(world)

    controller.connect(resume=True, world=world)
    controller.step(world, n=20)
    controller.save_and_close()

    real_node = controller.freeze_vehicle_and_locate(world, "V1")
    assert real_node  # grounded in a real position, not a static guess

    # Freezing alone never sets status=broken (by design -- the caller runs
    # the normal validated event for that); replicate the real `sim
    # breakdown` CLI flow so `step()`'s broken-vehicle speed enforcement
    # actually engages.
    interpreter = RuleBasedEventInterpreter()
    draft = interpreter.parse("Vozilo V1 je obstalo.", world.sim_time_s)
    validated = interpreter.validate_event(draft, world)
    apply_event(world, validated)
    assert world.vehicles["V1"].status.value == "broken"

    controller.connect(resume=True, world=world)
    # The freeze command is issued mid-motion; realistic deceleration takes
    # a step or two to actually reach zero speed (a few meters of final
    # braking distance -- physically honest, not a bug). Let that one-time
    # settling finish before asserting "never moves again".
    controller.step(world, n=5)
    before_edge, before_pos = world.vehicles["V1"].sumo_edge_id, world.vehicles["V1"].sumo_lane_position
    controller.step(world, n=30)
    after_edge, after_pos = world.vehicles["V1"].sumo_edge_id, world.vehicles["V1"].sumo_lane_position
    controller.save_and_close()

    assert after_edge == before_edge, "a frozen (broken) vehicle must never change edges"
    assert abs(after_pos - before_pos) < 1.0, "a frozen (broken) vehicle must never move again once settled"


def test_live_road_closure_blocks_routing(tmp_path):
    world = _one_task_world(tmp_path, pickup="A1", dropoff="A3")
    controller = _controller(tmp_path)
    controller.load_scenario(world)

    travel = controller._ensure_travel()
    original_path = travel.road_path_edges("A0", "A3")
    closed_edge = original_path[0]

    world.closed_edges[closed_edge] = None
    controller.apply_road_closure_physically(world, closed_edge)

    travel.set_closed_edges(world.closed_edges)
    new_path = travel.road_path_edges("A0", "A3")
    assert closed_edge not in new_path
