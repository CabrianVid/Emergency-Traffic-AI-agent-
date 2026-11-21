"""Canonical RescueFlow domain contracts.

All times are **simulation seconds** (`*_s` suffix) unless explicitly named
`wall_time_ms`. IDs are stable strings assigned by the scenario fixture or by
deterministic event handling (never random at read time, so scenarios replay
identically).

`WorldState.plan_revision` increments only on planning-affecting changes
(new/changed task, committed assignment, asset custody change, capacity
change, vehicle availability change). `WorldState.telemetry_seq` increments on
every simulator position tick and must never be used as a staleness check for
plan approval -- see `docs/CONTRACTS.md`.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0.0"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class VehicleMode(str, Enum):
    ambulance = "ambulance"
    helicopter = "helicopter"


class VehicleStatus(str, Enum):
    available = "available"
    assigned = "assigned"
    en_route_pickup = "en_route_pickup"
    loading = "loading"
    carrying = "carrying"
    unloading = "unloading"
    repositioning = "repositioning"
    broken = "broken"
    weather_grounded = "weather_grounded"
    offline = "offline"


class TaskKind(str, Enum):
    patient_transport = "patient_transport"
    equipment_delivery = "equipment_delivery"
    urgent_response = "urgent_response"
    equipment_recovery = "equipment_recovery"


class TaskPriority(str, Enum):
    critical = "critical"
    high = "high"
    routine = "routine"


class TaskStatus(str, Enum):
    pending = "pending"
    planned = "planned"
    en_route_pickup = "en_route_pickup"
    picked_up = "picked_up"
    en_route_dropoff = "en_route_dropoff"
    completed = "completed"
    interrupted = "interrupted"
    unserved = "unserved"
    requires_operator_intervention = "requires_operator_intervention"


class StopKind(str, Enum):
    pickup = "pickup"
    dropoff = "dropoff"
    standby = "standby"


class EventType(str, Enum):
    vehicle_breakdown = "vehicle_breakdown"
    new_urgent_task = "new_urgent_task"
    road_closure = "road_closure"
    priority_changed = "priority_changed"
    hospital_capacity_changed = "hospital_capacity_changed"
    helicopter_grounded = "helicopter_grounded"
    vehicle_delay = "vehicle_delay"
    vehicle_restored = "vehicle_restored"


class ReportedBy(str, Enum):
    dispatcher = "dispatcher"
    crew = "crew"
    simulation = "simulation"


# ---------------------------------------------------------------------------
# Stops, Vehicles, Tasks, Assets, Receiving sites
# ---------------------------------------------------------------------------


class Stop(_Base):
    """One ordered stop in a vehicle's committed or candidate route."""

    stop_id: str
    vehicle_id: str
    task_id: str | None = None
    kind: StopKind
    node_id: str
    arrival_s: float | None = None
    service_start_s: float | None = None
    departure_s: float | None = None
    earliest_s: float | None = None
    latest_s: float | None = None
    patient_load_after: int = 0
    equipment_load_after: int = 0
    committed: bool = False
    """True once physically executed or currently in progress -- never
    silently reassigned by a planner (see docs/CONTRACTS.md)."""
    service_completed_at_s: float | None = None
    """Set by `SimulationController` to the *real* sim time the dwell
    actually finished (overwriting the planner's estimate in `departure_s`
    at the same moment). None means not yet physically serviced -- this is
    how the controller rebuilds its pending-stop pointer after a
    save-state/load-state reconnect, see docs/ARCHITECTURE.md."""


class Vehicle(_Base):
    id: str
    mode: VehicleMode
    status: VehicleStatus = VehicleStatus.available
    node_id: str
    """Current road node (ambulance) or base/staging node (helicopter)."""
    patient_capacity: int = 0
    equipment_capacity_units: int = 0
    capabilities: list[str] = Field(default_factory=list)
    equipment_aboard: list[str] = Field(default_factory=list)
    """Asset IDs physically aboard this vehicle right now."""
    route_stops: list[Stop] = Field(default_factory=list)
    available_after_s: float = 0.0
    locked_stop_index: int | None = None
    """Index into route_stops of the stop currently being executed /
    committed in flight. Stops at or before this index must not be removed
    or reordered by a repair."""
    sumo_edge_id: str | None = None
    sumo_lane_position: float | None = None
    """Real simulated position (edge id + offset along it), written only by
    `SimulationController` while a live TraCI session is running -- distinct
    from `node_id`, which is the nearest/last *discrete* node and is what
    planning operates on. See docs/CONTRACTS.md "Real position vs
    planning node"."""

    def patient_load(self) -> int:
        if self.locked_stop_index is None or not self.route_stops:
            return 0
        return self.route_stops[self.locked_stop_index].patient_load_after


class Task(_Base):
    id: str
    kind: TaskKind
    priority: TaskPriority
    pickup_node: str
    dropoff_node: str
    created_at_s: float
    earliest_pickup_s: float | None = None
    latest_pickup_s: float | None = None
    earliest_delivery_s: float | None = None
    latest_delivery_s: float | None = None
    time_window_hard: bool = False
    load_seconds: float = 60.0
    unload_seconds: float = 60.0
    service_seconds: float = 0.0
    patient_slots: int = 0
    equipment_units: int = 0
    required_capabilities: list[str] = Field(default_factory=list)
    required_asset_ids: list[str] = Field(default_factory=list)
    allowed_modes: list[VehicleMode] = Field(default_factory=lambda: [VehicleMode.ambulance])
    status: TaskStatus = TaskStatus.pending
    predecessor_task_ids: list[str] = Field(default_factory=list)
    assigned_vehicle_id: str | None = None
    superseded_by: str | None = None
    """Set when this task was replaced by a recovery/continuation task."""


class AssetLocation(_Base):
    kind: Literal["vehicle", "depot", "hospital", "roadside"]
    ref_id: str
    """vehicle_id / depot_id / hospital_id / node_id, depending on `kind`."""


class Asset(_Base):
    id: str
    capacity_units: int = 1
    capability_tags: list[str] = Field(default_factory=list)
    location: AssetLocation
    status: Literal["available", "in_transit", "stranded"] = "available"


class ReceivingSlot(_Base):
    start_s: float
    end_s: float
    capacity: int


class ReceivingSite(_Base):
    id: str
    node_id: str
    accepted_task_kinds: list[TaskKind] = Field(default_factory=lambda: [TaskKind.patient_transport])
    slots: list[ReceivingSlot] = Field(default_factory=list)
    service_handling_s: float = 120.0

    def capacity_at(self, t_s: float) -> int | None:
        """Capacity for the interval containing t_s, or None if unconstrained."""
        if not self.slots:
            return None
        for slot in self.slots:
            if slot.start_s <= t_s < slot.end_s:
                return slot.capacity
        return 0


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class ScheduledRestoration(_Base):
    """A generic "at sim time T, undo this automatically" entry -- used for
    a 3600s fog grounding expiring, or a planned road closure reopening,
    without inventing a bespoke timer mechanism per event type."""

    kind: Literal["vehicle_status", "edge_reopen"]
    at_sim_s: float
    target_id: str
    """vehicle_id for `vehicle_status`, edge_id for `edge_reopen`."""
    restore_value: str | None = None
    """New VehicleStatus value for `vehicle_status`; unused for `edge_reopen`."""
    applied: bool = False


class Event(_Base):
    event_id: str
    sim_time_s: float
    type: EventType
    payload: dict = Field(default_factory=dict)
    source_text: str | None = None
    reported_by: ReportedBy = ReportedBy.dispatcher
    received_by_dispatcher_at_s: float
    status: Literal["received", "validated", "rejected", "applied"] = "received"
    impact_summary: str | None = None


# ---------------------------------------------------------------------------
# Feasibility, plans, evaluation
# ---------------------------------------------------------------------------


class FeasibilityIssue(_Base):
    code: str
    message: str
    task_id: str | None = None
    vehicle_id: str | None = None


class FeasibilityReport(_Base):
    feasible: bool
    issues: list[FeasibilityIssue] = Field(default_factory=list)


class ScoreBreakdown(_Base):
    unserved_critical: int = 0
    critical_tardiness_s: float = 0.0
    other_weighted_lateness_s: float = 0.0
    assignment_churn: int = 0
    extra_travel_s: float = 0.0

    def as_tuple(self) -> tuple[int, float, float, int, float]:
        """Lexicographic comparison key (lower is always better)."""
        return (
            self.unserved_critical,
            self.critical_tardiness_s,
            self.other_weighted_lateness_s,
            self.assignment_churn,
            self.extra_travel_s,
        )


class VehiclePlan(_Base):
    vehicle_id: str
    stops: list[Stop]


class PlanProposal(_Base):
    proposal_id: str
    based_on_plan_revision: int
    proposed_at_sim_s: float
    policy: Literal["b0", "a1"]
    profile: Literal["urgent_first", "minimum_disruption"] = "minimum_disruption"
    triggering_event_id: str | None = None
    vehicle_plans: list[VehiclePlan] = Field(default_factory=list)
    changed_task_ids: list[str] = Field(default_factory=list)
    unserved_task_ids: list[str] = Field(default_factory=list)
    feasibility: FeasibilityReport
    score: ScoreBreakdown
    explanation: str = ""
    ttl_s: float = 120.0
    committed: bool = False


class PlanEvaluation(_Base):
    proposal_id: str
    unserved_critical: int
    unserved_total: int
    weighted_tardiness_s: float
    mean_lateness_s: float
    p90_lateness_s: float
    assignment_churn: int
    extra_travel_s: float
    on_time_pct: float


class DispatchMessage(_Base):
    message_id: str
    unit_id: str
    task_id: str
    destination_label: str
    origin: Literal["baseline", "rescueflow"]
    plan_version: int
    sent_at_sim_s: float
    delivery_status: Literal["created", "mock_sent", "mock_acknowledged", "failed"] = "created"


class OperatorDecision(_Base):
    decision_id: str
    proposal_id: str
    approved_by: str
    decision_at_sim_s: float
    outcome: Literal["approved", "rejected", "regenerate"]
    idempotency_key: str
    reason: str | None = None


class SimulationSnapshot(_Base):
    telemetry_seq: int
    sim_time_s: float
    vehicle_positions: dict[str, dict] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# World state
# ---------------------------------------------------------------------------


class WorldState(_Base):
    schema_version: str = SCHEMA_VERSION
    plan_revision: int = 0
    telemetry_seq: int = 0
    sim_time_s: float = 0.0
    vehicles: dict[str, Vehicle] = Field(default_factory=dict)
    tasks: dict[str, Task] = Field(default_factory=dict)
    assets: dict[str, Asset] = Field(default_factory=dict)
    receiving_sites: dict[str, ReceivingSite] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    dispatch_messages: list[DispatchMessage] = Field(default_factory=list)
    decisions: list[OperatorDecision] = Field(default_factory=list)
    closed_edges: dict[str, float | None] = Field(default_factory=dict)
    """edge_id -> reopen_at_sim_s (None = indefinite), see events/apply.py
    `_apply_road_closure` and travel/sumo_travel_time.py closure-awareness."""
    scheduled_restorations: list[ScheduledRestoration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_match_keys(self) -> "WorldState":
        for key, vehicle in self.vehicles.items():
            if key != vehicle.id:
                raise ValueError(f"vehicle dict key {key!r} != vehicle.id {vehicle.id!r}")
        for key, task in self.tasks.items():
            if key != task.id:
                raise ValueError(f"task dict key {key!r} != task.id {task.id!r}")
        for key, asset in self.assets.items():
            if key != asset.id:
                raise ValueError(f"asset dict key {key!r} != asset.id {asset.id!r}")
        return self

    def bump_plan_revision(self) -> int:
        self.plan_revision += 1
        return self.plan_revision

    def bump_telemetry(self) -> int:
        self.telemetry_seq += 1
        return self.telemetry_seq
