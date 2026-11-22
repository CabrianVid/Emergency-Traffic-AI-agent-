"""B1 central-queue baseline, and calls that were unserved at first."""

from rescueflow.approval.service import DefaultApprovalService
from rescueflow.decision.alternatives import generate_alternatives, measure, planning_copy
from rescueflow.models.core import Stop, StopKind, Task, TaskKind, TaskPriority, TaskStatus, Vehicle, VehicleMode, WorldState
from rescueflow.planning.insertion import refresh_schedules
from rescueflow.planning.queue_dispatch import CentralQueuePlanner, FreeUnitTrigger, ready_pairs, waiting_calls
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


def _travel():
    travel = FakeTravelTimeProvider()
    travel.set_closed_edges = lambda closed: None
    return travel


def test_waiting_call_gets_an_estimated_response_so_waiting_never_looks_free():
    world = WorldState(vehicles={"R1": _unit("R1", "0,0")}, tasks={"C1": _call("C1", "10,0"), "C2": _call("C2", "20,0")})
    plan = CentralQueuePlanner(_travel()).plan(world, None)
    metrics = measure(world, plan, _travel())
    assert metrics["waiting_call_ids"] == ["C2"] and metrics["unserved_task_ids"] == []
    assert metrics["call_response_s"]["C2"] > metrics["call_response_s"]["C1"] > 0
    assert metrics["worst_critical_response_eta_s"] == metrics["critical_response_eta_s"]["C2"]


def test_free_unit_trigger_fires_once_per_newly_ready_unit():
    world = WorldState(vehicles={"R1": _unit("R1", "0,0")}, tasks={"C1": _call("C1", "10,0"), "C2": _call("C2", "20,0")})
    trigger = FreeUnitTrigger()
    DefaultApprovalService().approve(world, CentralQueuePlanner(_travel()).plan(world, None), "test", "k")
    trigger.mark(world)
    assert not trigger.fires(world, lambda t: True)
    for stop in world.vehicles["R1"].route_stops:
        stop.service_completed_at_s = 1.0
    assert trigger.fires(world, lambda t: True) and not trigger.fires(world, lambda t: True)


def test_unit_at_a_stop_keeps_its_task_and_schedules_start_from_now():
    world = _world_with_transport(TaskStatus.planned)
    world.sim_time_s = 600.0
    world.tasks["C"] = _call("C", "10,0")
    planning = planning_copy(world, _travel(), {"R1": 640.0})
    refresh_schedules(planning, _travel(), {"R1": 640.0})
    stops = planning.vehicles["R1"].route_stops
    assert stops[0].departure_s == 640.0 and stops[1].arrival_s > 640.0
    assert world.vehicles["R1"].route_stops[0].departure_s == 50.0, "the committed plan is untouched"
    assert CentralQueuePlanner(_travel()).plan(planning, None).vehicle_plans == []


def test_offered_options_include_the_queue_and_leave_unchanged_units_alone():
    world = WorldState(vehicles={"R1": _unit("R1", "0,0"), "R2": _unit("R2", "900,0")},
                       tasks={"C1": _call("C1", "10,0"), "H": _call("H", "890,0", TaskPriority.high)})
    options, _ = generate_alternatives(world, _travel())
    DefaultApprovalService().approve(world, options[0].proposal, "test", "k")
    world.sim_time_s = 5.0
    again, _ = generate_alternatives(world, _travel())
    assert [p for o in again if o.strategy == "waiting_queue" for p in o.proposal.vehicle_plans] == []


def test_critical_call_goes_to_the_unit_that_arrives_first_not_the_cheapest_detour():
    """R1 could add the call cheaply after its own critical patient; R2 is only on its way to a transfer."""
    world = WorldState(
        vehicles={"R1": _unit("R1", "0,0"), "R2": _unit("R2", "200,0")},
        tasks={"C1": _call("C1", "300,0"), "T": Task(
            id="T", kind=TaskKind.patient_transport, priority=TaskPriority.high, pickup_node="900,900",
            dropoff_node="0,0", created_at_s=0.0, patient_slots=1)})
    options, _ = generate_alternatives(world, _travel())
    DefaultApprovalService().approve(world, options[0].proposal, "test", "k")
    world.tasks["C2"] = _call("C2", "0,10")
    options, _ = generate_alternatives(world, _travel())
    urgent = next(o for o in options if o.strategy == "urgent_first")
    queue = [o for o in options if o.strategy == "waiting_queue"]
    best = min(o.metrics["mean_call_response_s"] for o in options)
    assert urgent.metrics["mean_call_response_s"] == best
    assert not queue or queue[0].metrics["mean_call_response_s"] >= best
