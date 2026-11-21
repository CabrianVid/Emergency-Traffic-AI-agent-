"""Pre-commit event types.

An `EventDraft` is an *unvalidated observation* -- it may name an entity that
does not exist, or claim a fact (e.g. "K1 is aboard R2") that conflicts with
authoritative world state. `validate_event` checks the draft against
`WorldState` and either returns a `ValidatedEvent` (safe to apply) or a
`ValidationError` (never silently accepted). See docs/CONTRACTS.md section on
event parsing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rescueflow.models.core import Event, EventType, ReportedBy


class EventDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EventType
    sim_time_s: float
    vehicle_id: str | None = None
    task_id: str | None = None
    equipment_ids_reported_stranded: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)
    source_text: str | None = None
    reported_by: ReportedBy = ReportedBy.dispatcher
    requires_confirmation: bool = False


class ValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    draft: EventDraft


class ValidatedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Event
    draft: EventDraft


class TaskImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    impact: str
    """Human-readable impact, e.g. 'interrupted, requires equipment recovery'."""
