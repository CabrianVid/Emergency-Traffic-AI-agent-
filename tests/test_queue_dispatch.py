"""B1 central-queue baseline, and calls that were unserved at first."""

from rescueflow.approval.service import DefaultApprovalService
from rescueflow.decision.alternatives import generate_alternatives
from rescueflow.models.core import Stop, StopKind, Task, TaskKind, TaskPriority, TaskStatus, Vehicle, VehicleMode, WorldState
from rescueflow.planning.queue_dispatch import CentralQueuePlanner, ready_pairs, waiting_calls
from test_planners import FakeTravelTimeProvider


def _call(task_id, node, priority=TaskPriority.critical, **fields):
    return Task(id=task_id, kind=TaskKind.urgent_response, priority=priority, pickup_node=node,
                dropoff_node="0,0", created_at_s=0.0, patient_slots=1, **fields)


def _unit(unit_id, node, **fields):
    return Vehicle(id=unit_id, mode=VehicleMode.ambulance, node_id=node, patient_capacity=1,
                   capabilities=["ALS"], **fields)


def _world_with_transport(status):
    """One unit whose only work is a routine transport in the given state."""
    transport = Task(id="TR", kind=TaskKind.patient_transport, priority=TaskPriority.routine, pickup_node="500,0",
                     dropoff_node="900,0", created_at_s=0.0, patient_slots=1, status=status, assigned_vehicle_id="R1")
    stops = [Stop(stop_id=k.value, vehicle_id="R1", task_id="TR", kind=k, node_id=n, arrival_s=a, departure_s=a)
             for k, n, a in ((StopKind.pickup, "500,0", 50.0), (StopKind.dropoff, "900,0", 90.0))]
    return WorldState(vehicles={"R1": _unit("R1", "0,0", route_stops=stops)}, tasks={"TR": transport})


def test_second_call_waits_instead_of_queueing_behind_a_busy_unit():
    world = WorldState(vehicles={"R1": _unit("R1", "0,0")}, tasks={"C1": _call("C1", "10,0"), "C2": _call("C2", "20,0")})
    plan = CentralQueuePlanner(FakeTravelTimeProvider()).plan(world, None)
    assert plan.feasibility.feasible and plan.changed_task_ids == ["C1"] and plan.unserved_task_ids == []
    DefaultApprovalService().approve(world, plan, "test", "k")
    assert [t.id for t in waiting_calls(world)] == ["C2"] and not ready_pairs(world)
    for stop in world.vehicles["R1"].route_stops:
        stop.service_completed_at_s = 1.0
    assert ready_pairs(world) == {("C2", "R1")}


def test_critical_call_diverts_unstarted_routine_transport_only():
    world = _world_with_transport(TaskStatus.planned)
    world.tasks["C"] = _call("C", "10,0")
    plan = CentralQueuePlanner(FakeTravelTimeProvider()).plan(world, None)
    assert [s.task_id for s in plan.vehicle_plans[0].stops] == ["C", "C", "TR", "TR"]

    started = _world_with_transport(TaskStatus.picked_up)
    started.tasks["C"] = _call("C", "10,0")
    assert CentralQueuePlanner(FakeTravelTimeProvider()).plan(started, None).vehicle_plans == []

    high = _world_with_transport(TaskStatus.planned)
    high.tasks["C"] = _call("C", "10,0", TaskPriority.high)
    assert CentralQueuePlanner(FakeTravelTimeProvider()).plan(high, None).vehicle_plans == []


def test_more_urgent_call_is_served_first_and_by_the_nearest_unit():
    world = WorldState(vehicles={"FAR": _unit("FAR", "900,0"), "NEAR": _unit("NEAR", "30,0")},
                       tasks={"H": _call("H", "20,0", TaskPriority.high), "C": _call("C", "10,0")})
    plans = CentralQueuePlanner(FakeTravelTimeProvider()).plan(world, None).vehicle_plans
    assert {p.vehicle_id: p.stops[0].task_id for p in plans} == {"NEAR": "C", "FAR": "H"}


def test_recalculating_reconsiders_a_call_that_was_unserved():
    world = WorldState(vehicles={"R1": _unit("R1", "0,0")}, tasks={"C": _call("C", "10,0", status=TaskStatus.unserved)})
    travel = FakeTravelTimeProvider()
    travel.set_closed_edges = lambda closed: None
    options, _ = generate_alternatives(world, travel)
    assert options and options[0].proposal.vehicle_plans[0].stops[0].task_id == "C"
    DefaultApprovalService().approve(world, options[0].proposal, "test", "k")
    assert world.tasks["C"].status == TaskStatus.planned
