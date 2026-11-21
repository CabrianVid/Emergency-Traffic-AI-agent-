"""Contracts between RescueFlow modules.

These are `typing.Protocol` definitions, not a microservices boundary: every
implementation in prompt 1 runs in-process inside the one Python package. The
point of writing them as protocols is that each can be swapped for a mock in
tests (e.g. a `TravelTimeProvider` stub with fixed ETAs) without the rest of
the system noticing, and that no module reaches past its contract (the
planner never calls TraCI directly; the LLM-backed `EventInterpreter` never
mutates `WorldState` directly -- see docs/CONTRACTS.md).
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rescueflow.events.types import EventDraft, ValidatedEvent, ValidationError
from rescueflow.models.core import (
    DispatchMessage,
    Event,
    FeasibilityReport,
    OperatorDecision,
    PlanEvaluation,
    PlanProposal,
    SimulationSnapshot,
    WorldState,
)

# ---------------------------------------------------------------------------
# Small result types owned by the interfaces layer
# ---------------------------------------------------------------------------


class CommitResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    committed: bool
    new_plan_revision: int | None = None
    decision: OperatorDecision | None = None
    dispatch_messages: list[DispatchMessage] = Field(default_factory=list)
    rejection_reason: str | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    based_on_plan_revision: int
    expires_at_sim_s: float
    summary: str


class PlanComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_ids: list[str]
    recommended_proposal_id: str | None
    notes: str


class ExplanationData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    summary: str
    changed_task_ids: list[str]
    unserved_task_ids: list[str]


class SmokeRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sumo_version: str
    duration_wall_ms: float
    sim_time_elapsed_s: float
    vehicles_arrived: int
    ok: bool
    detail: str = ""


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    policy: Literal["b0", "a1"]
    evaluation: PlanEvaluation


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ScenarioLoader(Protocol):
    def load(self, path: str) -> WorldState: ...

    def validate(self, world: WorldState) -> FeasibilityReport: ...


@runtime_checkable
class TravelTimeProvider(Protocol):
    def road_eta_s(self, origin_node: str, dest_node: str, depart_s: float) -> float:
        """Estimated travel time in seconds on the shared road network graph."""
        ...

    def road_route_exists(self, origin_node: str, dest_node: str) -> bool: ...

    def air_eta_s(self, origin_node: str, dest_node: str) -> float:
        """Analytical helicopter ETA: activation + takeoff/landing + distance/200kmh."""
        ...


@runtime_checkable
class Planner(Protocol):
    def baseline_repair(
        self, world: WorldState, event: Event | None, profile: str = "minimum_disruption"
    ) -> PlanProposal:
        """B0: nearest available/eligible/feasible unit, local repair only."""
        ...

    def optimized_repair(
        self, world: WorldState, event: Event | None, profiles: list[str]
    ) -> list[PlanProposal]:
        """A1: fleet-wide reinsertion + local search, one proposal per profile."""
        ...


@runtime_checkable
class PlanValidator(Protocol):
    def validate(self, world: WorldState, proposal: PlanProposal) -> FeasibilityReport: ...


@runtime_checkable
class ApprovalService(Protocol):
    def request_approval(self, proposal: PlanProposal) -> ApprovalRequest: ...

    def approve(
        self, world: WorldState, proposal: PlanProposal, approved_by: str, idempotency_key: str
    ) -> CommitResult:
        """Only mutates `world` if the revision/TTL/feasibility checks pass.
        Calling twice with the same idempotency_key must not double-apply."""
        ...


@runtime_checkable
class DispatchNotificationAdapter(Protocol):
    def notify(
        self, world: WorldState, vehicle_id: str, task_id: str, destination_label: str, plan_version: int
    ) -> DispatchMessage:
        """Mock outbound assignment message; never a real paging/SMS call."""
        ...


@runtime_checkable
class NavigationHandoff(Protocol):
    def build_url(self, destination_label: str) -> str:
        """Describes a production Google-Maps-URL handoff. Never used to
        route fictional SUMO vehicles; informational only in the MVP."""
        ...


@runtime_checkable
class SimulationController(Protocol):
    def load_scenario(self, world: WorldState) -> None: ...

    def step(self, n: int = 1) -> SimulationSnapshot: ...

    def run_smoke(self, duration_s: int) -> SmokeRunResult: ...

    def apply_committed_plan(self, world: WorldState) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class EventInterpreter(Protocol):
    def parse(self, message: str, sim_time_s: float) -> EventDraft: ...

    def validate_event(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError: ...


@runtime_checkable
class BenchmarkRunner(Protocol):
    def run(self, world: WorldState, policies: list[str]) -> list[BenchmarkResult]: ...
