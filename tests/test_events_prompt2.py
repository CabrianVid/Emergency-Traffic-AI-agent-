"""Prompt 2's four newly-executed event types: road_closure, vehicle_delay,
priority_changed, hospital_capacity_changed, helicopter_grounded -- typed in
prompt 1, validated+applied here. No SUMO needed (world-level only;
physical closure/freeze side effects are covered in test_sumo_execution.py)."""

from __future__ import annotations

from rescueflow.events.apply import apply_event, tick_scheduled_restorations
from rescueflow.events.interpreter import RuleBasedEventInterpreter
from rescueflow.events.types import EventDraft, ValidationError
from rescueflow.models.core import (
    EventType,
    ReceivingSite,
    ReceivingSlot,
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
    vehicle = Vehicle(id="R1", mode=VehicleMode.ambulance, node_id="A")
    heli = Vehicle(id="HELI_1", mode=VehicleMode.helicopter, node_id="A")
    site = ReceivingSite(id="H1", node_id="B", slots=[ReceivingSlot(start_s=0, end_s=1000, capacity=2)])
    return WorldState(vehicles={"R1": vehicle, "HELI_1": heli}, tasks={"T1": task, "T_DONE": done_task},
                       receiving_sites={"H1": site}, sim_time_s=10.0)


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


def test_hospital_capacity_changed_replaces_overlapping_slots():
    """Any existing slot overlapping the new window is replaced wholesale
    (not split into remaining sub-intervals) -- a documented simplification,
    not a silent loss: the new, narrower window's capacity is what matters
    for the next planning cycle."""
    world = _world()
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.hospital_capacity_changed, sim_time_s=world.sim_time_s,
                        payload={"site_id": "H1", "slots": [{"start_s": 500, "end_s": 900, "capacity": 0}]})
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    slots = world.receiving_sites["H1"].slots
    assert slots == [ReceivingSlot(start_s=500, end_s=900, capacity=0)]
    assert world.receiving_sites["H1"].capacity_at(600) == 0


def test_helicopter_grounded_rejects_non_helicopter():
    world = _world()
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.helicopter_grounded, sim_time_s=world.sim_time_s, vehicle_id="R1",
                        payload={"end_s": 3600.0, "cause": "fog"})
    result = interp.validate_event(draft, world)
    assert isinstance(result, ValidationError)
    assert result.code == "not_a_helicopter"


def test_helicopter_grounded_auto_restores_after_duration():
    world = _world()
    interp = RuleBasedEventInterpreter()
    draft = EventDraft(type=EventType.helicopter_grounded, sim_time_s=world.sim_time_s, vehicle_id="HELI_1",
                        payload={"end_s": world.sim_time_s + 3600.0, "cause": "fog"})
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)
    assert world.vehicles["HELI_1"].status == VehicleStatus.weather_grounded

    world.sim_time_s += 3601.0
    tick_scheduled_restorations(world)
    assert world.vehicles["HELI_1"].status == VehicleStatus.available
