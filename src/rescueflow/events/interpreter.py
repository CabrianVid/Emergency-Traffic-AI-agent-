"""Rule-based event interpreter (deterministic demo fallback / stage-1
implementation). Stage 3 may add a genuine LLM-backed interpreter behind the
same `EventInterpreter` protocol; both must converge on the same
`validate_event` path, and an absent LLM key must never block the CLI or
benchmark -- see docs/CONTRACTS.md 9.1.

`parse()` never fabricates a type it is not confident about: unrecognized
free text raises `EventParseError` so the caller can show "AI interpretation
unavailable" and fall back to structured scenario buttons (docs/CONTRACTS.md
9.6), rather than silently guessing at an event the operator never reported.
"""

from __future__ import annotations

import re

from rescueflow.events.types import EventDraft, TaskImpact, ValidatedEvent, ValidationError
from rescueflow.models.core import Event, EventType, ReportedBy, TaskStatus, WorldState

_BREAKDOWN_KEYWORDS = re.compile(r"(pokvar|obstal|breakdown|broke down|stalled)", re.IGNORECASE)
_VEHICLE_PATTERN = re.compile(r"[Vv]ozilo\s+([A-Za-z]+\d+)")
_EQUIPMENT_PATTERN = re.compile(r"[Kk]omplet\s+([A-Za-z]+\d+)")
_GENERIC_ID_PATTERN = re.compile(r"\b([A-Za-z]{1,3}\d{1,3})\b")


class EventParseError(Exception):
    """Raised when free text cannot be confidently classified. The caller
    must show a visible "AI interpretation unavailable" message and offer
    deterministic scenario buttons -- never substitute a guessed event."""


class RuleBasedEventInterpreter:
    """Concrete `EventInterpreter`."""

    def parse(self, message: str, sim_time_s: float) -> EventDraft:
        if _BREAKDOWN_KEYWORDS.search(message):
            vehicle_match = _VEHICLE_PATTERN.search(message)
            if not vehicle_match:
                raise EventParseError(
                    "Breakdown keyword found but no 'Vozilo <ID>' vehicle reference could be extracted."
                )
            vehicle_id = vehicle_match.group(1).upper()
            equipment_ids = [m.upper() for m in _EQUIPMENT_PATTERN.findall(message)]
            if not equipment_ids:
                equipment_ids = [
                    m.upper()
                    for m in _GENERIC_ID_PATTERN.findall(message)
                    if m.upper() != vehicle_id
                ]
            return EventDraft(
                type=EventType.vehicle_breakdown,
                sim_time_s=sim_time_s,
                vehicle_id=vehicle_id,
                equipment_ids_reported_stranded=equipment_ids,
                source_text=message,
                reported_by=ReportedBy.crew,
            )
        raise EventParseError(
            "Message did not match any supported free-text pattern in prompt 1 "
            "(only the vehicle-breakdown report is NLP-parsed). Use a structured "
            "scenario command instead."
        )

    def validate_event(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        if draft.type == EventType.vehicle_breakdown:
            return self._validate_breakdown(draft, world)
        if draft.type == EventType.new_urgent_task:
            return self._validate_new_urgent_task(draft, world)
        return ValidationError(
            code="not_implemented",
            message=f"Event type {draft.type.value!r} has a typed data model but its execution "
            "is planned for a later prompt (see docs/IMPLEMENTATION_STATUS.md).",
            draft=draft,
        )

    def _validate_breakdown(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        if draft.vehicle_id is None or draft.vehicle_id not in world.vehicles:
            return ValidationError(
                code="unknown_vehicle", message=f"vehicle {draft.vehicle_id!r} is not in the world state", draft=draft
            )
        vehicle = world.vehicles[draft.vehicle_id]
        if vehicle.status.value == "broken":
            return ValidationError(
                code="already_broken", message=f"vehicle {draft.vehicle_id} is already marked broken", draft=draft
            )

        for asset_id in draft.equipment_ids_reported_stranded:
            asset = world.assets.get(asset_id)
            if asset is None:
                return ValidationError(
                    code="unknown_asset", message=f"reported asset {asset_id!r} does not exist", draft=draft
                )
            if asset.location.kind != "vehicle" or asset.location.ref_id != draft.vehicle_id:
                return ValidationError(
                    code="asset_location_conflict",
                    message=f"asset {asset_id} was reported stranded on {draft.vehicle_id} but world state has it "
                    f"at {asset.location.kind}:{asset.location.ref_id}",
                    draft=draft,
                )

        affected = self._affected_task_impacts(world, draft.vehicle_id)
        impact_summary = (
            f"{draft.vehicle_id} unavailable; "
            + (", ".join(f"{i.task_id} ({i.impact})" for i in affected) if affected else "no in-progress tasks affected")
        )
        event = Event(
            event_id=f"evt-{draft.type.value}-{draft.vehicle_id}-{int(draft.sim_time_s)}",
            sim_time_s=draft.sim_time_s,
            type=draft.type,
            payload={"vehicle_id": draft.vehicle_id, "equipment_ids": draft.equipment_ids_reported_stranded},
            source_text=draft.source_text,
            reported_by=draft.reported_by,
            received_by_dispatcher_at_s=world.sim_time_s,
            status="validated",
            impact_summary=impact_summary,
        )
        return ValidatedEvent(event=event, draft=draft)

    def _validate_new_urgent_task(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        payload = draft.payload
        required = {"pickup_node", "dropoff_node", "priority"}
        missing = required - payload.keys()
        if missing:
            return ValidationError(
                code="missing_fields", message=f"new_urgent_task payload missing {sorted(missing)}", draft=draft
            )
        if payload["pickup_node"] == payload["dropoff_node"]:
            return ValidationError(
                code="degenerate_task", message="pickup_node and dropoff_node must differ", draft=draft
            )
        event = Event(
            event_id=f"evt-new_urgent_task-{int(draft.sim_time_s)}",
            sim_time_s=draft.sim_time_s,
            type=draft.type,
            payload=payload,
            source_text=draft.source_text,
            reported_by=draft.reported_by,
            received_by_dispatcher_at_s=world.sim_time_s,
            status="validated",
            impact_summary="new urgent task will be inserted into the next replan",
        )
        return ValidatedEvent(event=event, draft=draft)

    @staticmethod
    def _affected_task_impacts(world: WorldState, vehicle_id: str) -> list[TaskImpact]:
        impacts = []
        for task in world.tasks.values():
            if task.assigned_vehicle_id != vehicle_id:
                continue
            if task.status in (TaskStatus.completed, TaskStatus.unserved, TaskStatus.interrupted):
                continue
            impacts.append(TaskImpact(task_id=task.id, impact="interrupted, requires replanning"))
        return impacts
