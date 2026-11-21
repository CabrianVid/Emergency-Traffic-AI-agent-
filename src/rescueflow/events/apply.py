"""Committing a validated Event to `WorldState`.

This is deliberately separate from plan approval: recognizing that R2 broke
down (and that K1 is now stranded roadside) is an **observed fact**, applied
automatically once validated (docs/CONTRACTS.md 9.5 -- "event detection and
interpretation: automatic, with validation"). It does *not* decide who
recovers K1; that is a task/vehicle *assignment*, which stays pending until a
planner proposes it and a dispatcher approves it. Applying an event always
increments `plan_revision` (it changes what any subsequent proposal must be
based on) and is independent of `telemetry_seq`.
"""

from __future__ import annotations

from rescueflow.events.types import ValidatedEvent
from rescueflow.models.core import (
    AssetLocation,
    EventType,
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    VehicleStatus,
    WorldState,
)

RECOVERY_TRANSFER_SECONDS = 180.0

_ACTIVE_TASK_STATUSES = (
    TaskStatus.planned,
    TaskStatus.en_route_pickup,
    TaskStatus.picked_up,
    TaskStatus.en_route_dropoff,
)


def apply_event(world: WorldState, validated: ValidatedEvent) -> None:
    """Mutates `world` in place and appends the committed Event. Raises
    NotImplementedError for event types whose typed model exists but whose
    execution is not implemented in prompt 1."""
    event = validated.event
    if event.type == EventType.vehicle_breakdown:
        _apply_vehicle_breakdown(world, event)
    elif event.type == EventType.new_urgent_task:
        _apply_new_urgent_task(world, event)
    else:
        raise NotImplementedError(
            f"{event.type.value} has a typed model but no execution path in prompt 1"
        )
    world.events.append(event)
    world.bump_plan_revision()


def _apply_vehicle_breakdown(world: WorldState, event) -> None:
    vehicle_id = event.payload["vehicle_id"]
    vehicle = world.vehicles[vehicle_id]
    vehicle.status = VehicleStatus.broken
    # `status == broken` alone excludes the vehicle from every eligibility
    # and validator check (see planning/insertion.py, planning/validator.py);
    # available_after_s is left as-is rather than set to infinity, which
    # does not round-trip through strict JSON.

    affected_tasks = [
        t for t in world.tasks.values()
        if t.assigned_vehicle_id == vehicle_id and t.status in _ACTIVE_TASK_STATUSES
    ]

    for task in affected_tasks:
        patient_physically_aboard = task.patient_slots > 0 and task.status in (
            TaskStatus.picked_up,
            TaskStatus.en_route_dropoff,
        )
        if patient_physically_aboard:
            # Never invent a mid-transport patient handoff -- escalate.
            task.status = TaskStatus.requires_operator_intervention
            continue

        equipment_physically_aboard = task.equipment_units > 0 and task.status in (
            TaskStatus.picked_up,
            TaskStatus.en_route_dropoff,
        )
        if equipment_physically_aboard:
            recovery = Task(
                id=f"{task.id}-RECOVERY",
                kind=TaskKind.equipment_recovery,
                priority=task.priority,
                pickup_node=vehicle.node_id,
                dropoff_node=task.dropoff_node,
                created_at_s=world.sim_time_s,
                earliest_delivery_s=task.earliest_delivery_s,
                latest_delivery_s=task.latest_delivery_s,
                time_window_hard=task.time_window_hard,
                load_seconds=RECOVERY_TRANSFER_SECONDS,
                unload_seconds=task.unload_seconds,
                equipment_units=task.equipment_units,
                required_asset_ids=list(task.required_asset_ids),
                allowed_modes=list(task.allowed_modes),
                status=TaskStatus.pending,
            )
            world.tasks[recovery.id] = recovery
            task.status = TaskStatus.interrupted
            task.superseded_by = recovery.id
            task.assigned_vehicle_id = None

            for asset_id in task.required_asset_ids:
                asset = world.assets.get(asset_id)
                if asset is not None:
                    asset.location = AssetLocation(kind="roadside", ref_id=vehicle.node_id)
                    asset.status = "stranded"
        else:
            # Not yet physically picked up -- simply returns to the pending pool.
            task.status = TaskStatus.pending
            task.assigned_vehicle_id = None

    if vehicle.locked_stop_index is not None:
        vehicle.route_stops = vehicle.route_stops[: vehicle.locked_stop_index]
    vehicle.locked_stop_index = None
    vehicle.equipment_aboard = []


def _apply_new_urgent_task(world: WorldState, event) -> None:
    payload = event.payload
    task = Task(
        id=payload.get("task_id") or f"urgent-{event.event_id}",
        kind=TaskKind.urgent_response,
        priority=TaskPriority(payload.get("priority", "critical")),
        pickup_node=payload["pickup_node"],
        dropoff_node=payload["dropoff_node"],
        created_at_s=event.sim_time_s,
        latest_pickup_s=payload.get("latest_pickup_s"),
        latest_delivery_s=payload.get("latest_delivery_s"),
        time_window_hard=payload.get("time_window_hard", False),
        patient_slots=payload.get("patient_slots", 1),
        required_capabilities=payload.get("required_capabilities", []),
        status=TaskStatus.pending,
    )
    world.tasks[task.id] = task
