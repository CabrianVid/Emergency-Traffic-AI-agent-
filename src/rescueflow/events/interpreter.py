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
        validator = self._VALIDATORS.get(draft.type)
        if validator is None:
            return ValidationError(
                code="not_supported",
                message=f"Event type {draft.type.value!r} is not part of the active application "
                "(helicopter and hospital-capacity events were removed; see docs/DECISIONS.md).",
                draft=draft,
            )
        return validator(self, draft, world)

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

    def _validate_road_closure(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        payload = draft.payload
        edge_id = payload.get("edge_id")
        if not edge_id:
            return ValidationError(code="missing_fields", message="road_closure payload needs 'edge_id'", draft=draft)
        if edge_id in world.closed_edges:
            return ValidationError(code="already_closed", message=f"edge {edge_id!r} is already closed", draft=draft)
        duration_s = payload.get("duration_s")
        reopen_at_s = payload.get("reopen_at_s")
        if duration_s is not None and reopen_at_s is not None:
            return ValidationError(
                code="ambiguous_duration", message="give at most one of duration_s/reopen_at_s", draft=draft
            )
        # Edge existence against the real SUMO network is checked where the
        # closure is actually used (TravelTimeProvider / SimulationController),
        # not here -- the event layer only knows WorldState, not the network
        # graph. See docs/CONTRACTS.md "Event validation layering".
        event = Event(
            event_id=f"evt-road_closure-{edge_id}-{int(draft.sim_time_s)}", sim_time_s=draft.sim_time_s,
            type=draft.type, payload=payload, source_text=draft.source_text, reported_by=draft.reported_by,
            received_by_dispatcher_at_s=world.sim_time_s, status="validated",
            impact_summary=f"edge {edge_id} closed; both B0 and A1 will detour around it on their next replan",
        )
        return ValidatedEvent(event=event, draft=draft)

    def _validate_vehicle_delay(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        if draft.vehicle_id is None or draft.vehicle_id not in world.vehicles:
            return ValidationError(code="unknown_vehicle", message=f"vehicle {draft.vehicle_id!r} is not in the world state", draft=draft)
        delay_s = draft.payload.get("delay_s")
        if not isinstance(delay_s, (int, float)) or delay_s <= 0:
            return ValidationError(code="invalid_delay", message="vehicle_delay payload needs a positive numeric 'delay_s'", draft=draft)
        event = Event(
            event_id=f"evt-vehicle_delay-{draft.vehicle_id}-{int(draft.sim_time_s)}", sim_time_s=draft.sim_time_s,
            type=draft.type, payload={"vehicle_id": draft.vehicle_id, "delay_s": delay_s},
            source_text=draft.source_text, reported_by=draft.reported_by, received_by_dispatcher_at_s=world.sim_time_s,
            status="validated", impact_summary=f"{draft.vehicle_id} delayed {delay_s}s; downstream stops shift",
        )
        return ValidatedEvent(event=event, draft=draft)

    def _validate_priority_changed(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        if draft.task_id is None or draft.task_id not in world.tasks:
            return ValidationError(code="unknown_task", message=f"task {draft.task_id!r} is not in the world state", draft=draft)
        task = world.tasks[draft.task_id]
        if task.status == TaskStatus.completed:
            return ValidationError(code="task_already_completed", message="cannot change priority of a completed task", draft=draft)
        new_priority = draft.payload.get("new_priority")
        reason = draft.payload.get("reason")
        if new_priority not in ("critical", "high", "routine"):
            return ValidationError(code="invalid_priority", message=f"unknown priority {new_priority!r}", draft=draft)
        if not reason:
            return ValidationError(code="missing_reason", message="priority_changed requires a non-empty verified 'reason'", draft=draft)
        event = Event(
            event_id=f"evt-priority_changed-{draft.task_id}-{int(draft.sim_time_s)}", sim_time_s=draft.sim_time_s,
            type=draft.type, payload={"task_id": draft.task_id, "new_priority": new_priority, "reason": reason},
            source_text=draft.source_text, reported_by=draft.reported_by, received_by_dispatcher_at_s=world.sim_time_s,
            status="validated",
            impact_summary=f"{draft.task_id} priority {task.priority.value} -> {new_priority} ({reason})",
        )
        return ValidatedEvent(event=event, draft=draft)

    def _validate_destination_changed(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        task = world.tasks.get(draft.task_id or "")
        if task is None:
            return ValidationError(code="unknown_task", message=f"task {draft.task_id!r} is not in the world state", draft=draft)
        if task.status in (TaskStatus.completed, TaskStatus.interrupted, TaskStatus.unserved):
            return ValidationError(code="task_closed", message=f"{task.id} is {task.status.value}", draft=draft)
        new_node = draft.payload.get("new_dropoff_node")
        if not new_node or new_node == task.dropoff_node:
            return ValidationError(code="invalid_destination",
                                   message="destination_changed needs a different 'new_dropoff_node'", draft=draft)
        if new_node == task.pickup_node and task.status != TaskStatus.en_route_dropoff:
            return ValidationError(code="degenerate_task", message="new destination equals the pickup", draft=draft)
        reason = draft.payload.get("reason", "destination changed")
        event = Event(
            event_id=f"evt-destination_changed-{task.id}-{int(draft.sim_time_s)}", sim_time_s=draft.sim_time_s,
            type=draft.type, payload={"task_id": task.id, "new_dropoff_node": new_node, "reason": reason},
            source_text=draft.source_text, reported_by=draft.reported_by, received_by_dispatcher_at_s=world.sim_time_s,
            status="validated", impact_summary=f"{task.id} now goes to {new_node} ({reason})",
        )
        return ValidatedEvent(event=event, draft=draft)

    def _validate_equipment_request(self, draft: EventDraft, world: WorldState) -> ValidatedEvent | ValidationError:
        payload = draft.payload
        asset = world.assets.get(payload.get("asset_id", ""))
        if asset is None:
            return ValidationError(code="unknown_asset", message=f"asset {payload.get('asset_id')!r} does not exist",
                                   draft=draft)
        if asset.location.kind == "vehicle":
            return ValidationError(code="asset_in_vehicle", message=f"{asset.id} is aboard {asset.location.ref_id}",
                                   draft=draft)
        open_statuses = (TaskStatus.completed, TaskStatus.interrupted, TaskStatus.unserved)
        if any(t.status not in open_statuses and asset.id in t.required_asset_ids for t in world.tasks.values()):
            return ValidationError(code="asset_already_tasked", message=f"{asset.id} already has an open task",
                                   draft=draft)
        if not payload.get("task_id") or payload["task_id"] in world.tasks:
            return ValidationError(code="invalid_task_id", message="equipment_request needs a new 'task_id'", draft=draft)
        if not payload.get("dropoff_node") or payload["dropoff_node"] == asset.location.ref_id:
            return ValidationError(code="invalid_destination",
                                   message="equipment_request needs a different 'dropoff_node'", draft=draft)
        if payload.get("priority", "high") not in ("critical", "high", "routine"):
            return ValidationError(code="invalid_priority", message="unknown priority", draft=draft)
        event = Event(
            event_id=f"evt-equipment_request-{payload['task_id']}-{int(draft.sim_time_s)}", sim_time_s=draft.sim_time_s,
            type=draft.type, payload=payload, source_text=draft.source_text, reported_by=draft.reported_by,
            received_by_dispatcher_at_s=world.sim_time_s, status="validated",
            impact_summary=f"{asset.id} must be collected at {asset.location.ref_id} and delivered to "
            f"{payload['dropoff_node']}",
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


RuleBasedEventInterpreter._VALIDATORS = {
    EventType.vehicle_breakdown: RuleBasedEventInterpreter._validate_breakdown,
    EventType.new_urgent_task: RuleBasedEventInterpreter._validate_new_urgent_task,
    EventType.road_closure: RuleBasedEventInterpreter._validate_road_closure,
    EventType.vehicle_delay: RuleBasedEventInterpreter._validate_vehicle_delay,
    EventType.priority_changed: RuleBasedEventInterpreter._validate_priority_changed,
    EventType.destination_changed: RuleBasedEventInterpreter._validate_destination_changed,
    EventType.equipment_request: RuleBasedEventInterpreter._validate_equipment_request,
}
