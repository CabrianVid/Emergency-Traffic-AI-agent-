"""Event parsing/validation/application: the R2+K1 breakdown example from
the challenge brief, plus the safety-critical "never invent a patient
handoff" escalation path."""

from __future__ import annotations

import pytest

from rescueflow.events.apply import apply_event
from rescueflow.events.interpreter import EventParseError, RuleBasedEventInterpreter
from rescueflow.events.types import EventDraft, ValidationError
from rescueflow.models.core import (
    Asset,
    AssetLocation,
    EventType,
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehicleMode,
    VehicleStatus,
    WorldState,
)


def _breakdown_world() -> WorldState:
    r2 = Vehicle(id="R2", mode=VehicleMode.ambulance, node_id="B1", status=VehicleStatus.carrying,
                 equipment_capacity_units=2, equipment_aboard=["K1"], locked_stop_index=1)
    task = Task(id="T3", kind=TaskKind.equipment_delivery, priority=TaskPriority.high, pickup_node="B1",
                dropoff_node="H1", created_at_s=0.0, equipment_units=1, required_asset_ids=["K1"],
                status=TaskStatus.en_route_dropoff, assigned_vehicle_id="R2")
    k1 = Asset(id="K1", location=AssetLocation(kind="vehicle", ref_id="R2"), status="in_transit")
    world = WorldState(vehicles={"R2": r2}, tasks={"T3": task}, assets={"K1": k1}, sim_time_s=120.0)
    world.vehicles["R2"].route_stops = []  # times are irrelevant to this test
    return world


def test_parse_slovenian_breakdown_report():
    interp = RuleBasedEventInterpreter()
    draft = interp.parse("Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.", sim_time_s=120.0)
    assert draft.type == EventType.vehicle_breakdown
    assert draft.vehicle_id == "R2"
    assert draft.equipment_ids_reported_stranded == ["K1"]


def test_unrecognized_free_text_raises_instead_of_guessing():
    interp = RuleBasedEventInterpreter()
    with pytest.raises(EventParseError):
        interp.parse("The weather is nice today.", sim_time_s=0.0)


def test_unknown_vehicle_is_rejected_not_silently_applied():
    world = _breakdown_world()
    draft = EventDraft(type=EventType.vehicle_breakdown, sim_time_s=120.0, vehicle_id="R99")
    result = RuleBasedEventInterpreter().validate_event(draft, world)
    assert isinstance(result, ValidationError)
    assert result.code == "unknown_vehicle"


def test_reported_asset_location_conflict_is_flagged_not_overwritten():
    world = _breakdown_world()
    world.assets["K1"].location = AssetLocation(kind="vehicle", ref_id="R1")  # actually on a different vehicle
    draft = EventDraft(type=EventType.vehicle_breakdown, sim_time_s=120.0, vehicle_id="R2",
                        equipment_ids_reported_stranded=["K1"])
    result = RuleBasedEventInterpreter().validate_event(draft, world)
    assert isinstance(result, ValidationError)
    assert result.code == "asset_location_conflict"
    # The world's authoritative record must be untouched.
    assert world.assets["K1"].location.ref_id == "R1"


def test_breakdown_creates_recovery_task_and_strands_equipment():
    world = _breakdown_world()
    interp = RuleBasedEventInterpreter()
    draft = interp.parse("Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.", world.sim_time_s)
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    assert world.vehicles["R2"].status == VehicleStatus.broken
    assert world.tasks["T3"].status == TaskStatus.interrupted
    assert world.tasks["T3"].superseded_by == "T3-RECOVERY"
    recovery = world.tasks["T3-RECOVERY"]
    assert recovery.status == TaskStatus.pending
    assert recovery.required_asset_ids == ["K1"]
    assert recovery.pickup_node == "B1"
    assert recovery.dropoff_node == "H1"
    assert world.assets["K1"].location.kind == "roadside"
    assert world.assets["K1"].status == "stranded"


def test_occupied_ambulance_breakdown_escalates_instead_of_inventing_handoff():
    r2 = Vehicle(id="R2", mode=VehicleMode.ambulance, node_id="B1", status=VehicleStatus.carrying,
                 patient_capacity=1, locked_stop_index=0)
    patient_task = Task(id="T_PATIENT", kind=TaskKind.patient_transport, priority=TaskPriority.critical,
                         pickup_node="B1", dropoff_node="H1", created_at_s=0.0, patient_slots=1,
                         status=TaskStatus.picked_up, assigned_vehicle_id="R2")
    world = WorldState(vehicles={"R2": r2}, tasks={"T_PATIENT": patient_task}, sim_time_s=120.0)

    interp = RuleBasedEventInterpreter()
    draft = interp.parse("Vozilo R2 je obstalo.", world.sim_time_s)
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    assert world.tasks["T_PATIENT"].status == TaskStatus.requires_operator_intervention
    assert world.tasks["T_PATIENT"].superseded_by is None
    assert "T_PATIENT-RECOVERY" not in world.tasks  # no invented handoff task
