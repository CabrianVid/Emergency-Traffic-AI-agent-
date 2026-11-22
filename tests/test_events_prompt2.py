"""Structured unexpected events: road_closure, vehicle_delay, priority_changed,
destination_changed and equipment_request; removed helicopter and
hospital-capacity events are rejected. No SUMO needed (world-level only;
physical closure/freeze side effects are covered in test_sumo_execution.py)."""

from __future__ import annotations

from rescueflow.events.apply import apply_event, tick_scheduled_restorations
from rescueflow.events.interpreter import RuleBasedEventInterpreter
from rescueflow.events.types import EventDraft, ValidationError
from rescueflow.models.core import (
    EventType,
    Asset,
    AssetLocation,
    Stop,
    StopKind,
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehicleMode,
    VehicleStatus,
    WorldState,
)


def _world() -> WorldState:
    task = Task(id="T1", kind=TaskKind.patient_transport, priority=TaskPriority.routine,
                pickup_node="A", dropoff_node="B", created_at_s=0.0, patient_slots=1)
    done_task = Task(id="T_DONE", kind=TaskKind.patient_transport, priority=TaskPriority.routine,
                      pickup_node="A", dropoff_node="B", created_at_s=0.0, patient_slots=1,
                      status=TaskStatus.completed)
    vehicle = Vehicle(id="R1", mode=VehicleMode.ambulance, node_id="A", patient_capacity=1, route_stops=[
        Stop(stop_id="p", vehicle_id="R1", task_id="T1", kind=StopKind.pickup, node_id="A", arrival_s=20),
        Stop(stop_id="d", vehicle_id="R1", task_id="T1", kind=StopKind.dropoff, node_id="B", arrival_s=90),
    ])
    task.assigned_vehicle_id, task.status = "R1", TaskStatus.planned
    kit = Asset(id="K2", location=AssetLocation(kind="depot", ref_id="C"))
    return WorldState(vehicles={"R1": vehicle}, tasks={"T1": task, "T_DONE": done_task},
                      assets={"K2": kit}, sim_time_s=10.0)


def test_road_closure_validated_and_applied_with_scheduled_reopen():
    world = _world()
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.road_closure, sim_time_s=world.sim_time_s,
                        payload={"edge_id": "AB", "duration_s": 100.0})
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    assert "AB" in world.closed_edges
    assert world.closed_edges["AB"] == 110.0
    assert len(world.scheduled_restorations) == 1

    world.sim_time_s = 111.0
    applied = tick_scheduled_restorations(world)
    assert len(applied) == 1
    assert "AB" not in world.closed_edges


def test_road_closure_rejects_duplicate():
    world = _world()
    world.closed_edges["AB"] = None
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.road_closure, sim_time_s=world.sim_time_s, payload={"edge_id": "AB"})
    result = interp.validate_event(draft, world)
    assert isinstance(result, ValidationError)
    assert result.code == "already_closed"


def test_vehicle_delay_shifts_unlocked_stops():
    from rescueflow.models.core import Stop, StopKind

    world = _world()
    world.vehicles["R1"].route_stops = [
        Stop(stop_id="s1", vehicle_id="R1", task_id="T1", kind=StopKind.pickup, node_id="A",
             arrival_s=50.0, departure_s=60.0),
    ]
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.vehicle_delay, sim_time_s=world.sim_time_s, vehicle_id="R1",
                        payload={"delay_s": 30.0})
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    stop = world.vehicles["R1"].route_stops[0]
    assert stop.arrival_s == 80.0
    assert stop.departure_s == 90.0


def test_priority_changed_rejects_completed_task():
    world = _world()
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.priority_changed, sim_time_s=world.sim_time_s, task_id="T_DONE",
                        payload={"new_priority": "critical", "reason": "x"})
    result = interp.validate_event(draft, world)
    assert isinstance(result, ValidationError)
    assert result.code == "task_already_completed"


def test_priority_changed_requires_a_reason():
    world = _world()
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.priority_changed, sim_time_s=world.sim_time_s, task_id="T1",
                        payload={"new_priority": "critical", "reason": ""})
    result = interp.validate_event(draft, world)
    assert isinstance(result, ValidationError)
    assert result.code == "missing_reason"


def test_destination_change_moves_task_and_unserved_dropoff_stop():
    world = _world()
    draft = EventDraft(type=EventType.destination_changed, sim_time_s=world.sim_time_s, task_id="T1",
                       payload={"new_dropoff_node": "D", "reason": "trauma centre requested"})
    result = RuleBasedEventInterpreter().validate_event(draft, world)
    assert not isinstance(result, ValidationError), result
    apply_event(world, result)
    assert world.tasks["T1"].dropoff_node == "D"
    assert world.vehicles["R1"].route_stops[1].node_id == "D"


def test_destination_change_rejects_completed_task_and_same_node():
    world = _world()
    interpreter = RuleBasedEventInterpreter()
    closed = interpreter.validate_event(EventDraft(type=EventType.destination_changed, sim_time_s=10.0,
                                                   task_id="T_DONE", payload={"new_dropoff_node": "D"}), world)
    same = interpreter.validate_event(EventDraft(type=EventType.destination_changed, sim_time_s=10.0,
                                                 task_id="T1", payload={"new_dropoff_node": "B"}), world)
    assert isinstance(closed, ValidationError) and closed.code == "task_closed"
    assert isinstance(same, ValidationError) and same.code == "invalid_destination"


def test_equipment_request_creates_pickup_where_the_asset_is():
    world = _world()
    draft = EventDraft(type=EventType.equipment_request, sim_time_s=10.0,
                       payload={"task_id": "EQ-1", "asset_id": "K2", "dropoff_node": "B", "priority": "high"})
    result = RuleBasedEventInterpreter().validate_event(draft, world)
    assert not isinstance(result, ValidationError), result
    apply_event(world, result)
    task = world.tasks["EQ-1"]
    assert task.kind == TaskKind.equipment_delivery and task.pickup_node == "C"
    assert task.required_asset_ids == ["K2"] and task.status == TaskStatus.pending
    again = RuleBasedEventInterpreter().validate_event(
        draft.model_copy(update={"payload": {**draft.payload, "task_id": "EQ-2"}}), world)
    assert isinstance(again, ValidationError) and again.code == "asset_already_tasked"


def test_removed_helicopter_and_capacity_events_are_rejected():
    world = _world()
    interpreter = RuleBasedEventInterpreter()
    for event_type in (EventType.helicopter_grounded, EventType.hospital_capacity_changed):
        result = interpreter.validate_event(EventDraft(type=event_type, sim_time_s=10.0), world)
        assert isinstance(result, ValidationError) and result.code == "not_supported"
