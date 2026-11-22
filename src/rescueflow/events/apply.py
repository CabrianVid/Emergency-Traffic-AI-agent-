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
    ScheduledRestoration,
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
    handler = _HANDLERS.get(event.type)
    if handler is None:
        raise NotImplementedError(
            f"{event.type.value} has a typed model but no execution path yet"
        )
    handler(world, event)
    world.events.append(event)
    world.bump_plan_revision()


def tick_scheduled_restorations(world: WorldState) -> list[ScheduledRestoration]:
    """Called with every `world.sim_time_s` advance (by
    `SimulationController.step` and by the CLI's `sim advance`): applies any
    `ScheduledRestoration` whose time has arrived (e.g. HELI_1's 3600s fog
    grounding expiring, or a timed road closure reopening) and bumps
    `plan_revision` once if anything changed. Returns the restorations that
    were just applied, for logging."""
    applied: list[ScheduledRestoration] = []
    for restoration in world.scheduled_restorations:
        if restoration.applied or restoration.at_sim_s > world.sim_time_s:
            continue
        if restoration.kind == "vehicle_status":
            vehicle = world.vehicles.get(restoration.target_id)
            if vehicle is not None and restoration.restore_value is not None:
                vehicle.status = VehicleStatus(restoration.restore_value)
        elif restoration.kind == "edge_reopen":
            world.closed_edges.pop(restoration.target_id, None)
        restoration.applied = True
        applied.append(restoration)
    if applied:
        world.bump_plan_revision()
    return applied


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


def _apply_road_closure(world: WorldState, event) -> None:
    payload = event.payload
    edge_id = payload["edge_id"]
    duration_s = payload.get("duration_s")
    reopen_at_s = payload.get("reopen_at_s")
    if reopen_at_s is None and duration_s is not None:
        reopen_at_s = world.sim_time_s + duration_s
    world.closed_edges[edge_id] = reopen_at_s
    if reopen_at_s is not None:
        world.scheduled_restorations.append(
            ScheduledRestoration(kind="edge_reopen", at_sim_s=reopen_at_s, target_id=edge_id)
        )
    # Any tasks whose *unstarted* stops route through the closed edge are
    # left for the next planner call to detour around -- `TravelTimeProvider`
    # (once `set_closed_edges` is called with the updated world) will raise
    # on a path that has no open alternative, which the planner's insertion
    # search already treats as "no feasible candidate", never a crash.


def _apply_vehicle_delay(world: WorldState, event) -> None:
    payload = event.payload
    vehicle = world.vehicles[payload["vehicle_id"]]
    delay_s = float(payload["delay_s"])
    vehicle.available_after_s += delay_s
    start = 0 if vehicle.locked_stop_index is None else vehicle.locked_stop_index + 1
    for stop in vehicle.route_stops[start:]:
        if stop.arrival_s is not None:
            stop.arrival_s += delay_s
        if stop.service_start_s is not None:
            stop.service_start_s += delay_s
        if stop.departure_s is not None:
            stop.departure_s += delay_s


def _apply_priority_changed(world: WorldState, event) -> None:
    payload = event.payload
    task = world.tasks[payload["task_id"]]
    task.priority = TaskPriority(payload["new_priority"])


def _apply_destination_changed(world: WorldState, event) -> None:
    """The patient (or load) must go somewhere else. The task's drop-off and
    any not-yet-served drop-off stop for it move to the new node; the unit
    keeps its assignment and the controller reroutes it."""
    payload = event.payload
    task = world.tasks[payload["task_id"]]
    task.dropoff_node = payload["new_dropoff_node"]
    for vehicle in world.vehicles.values():
        for stop in vehicle.route_stops:
            if stop.task_id == task.id and stop.kind.value == "dropoff" and stop.service_completed_at_s is None:
                stop.node_id = task.dropoff_node


def _apply_equipment_request(world: WorldState, event) -> None:
    """Equipment must be fetched from where it physically is and delivered."""
    payload = event.payload
    asset = world.assets[payload["asset_id"]]
    task = Task(
        id=payload["task_id"], kind=TaskKind.equipment_delivery,
        priority=TaskPriority(payload.get("priority", "high")),
        pickup_node=asset.location.ref_id, dropoff_node=payload["dropoff_node"],
        created_at_s=event.sim_time_s, latest_delivery_s=payload.get("latest_delivery_s"),
        equipment_units=asset.capacity_units, required_asset_ids=[asset.id], patient_slots=0,
        load_seconds=60.0, unload_seconds=60.0, status=TaskStatus.pending,
    )
    world.tasks[task.id] = task


_HANDLERS = {
    EventType.vehicle_breakdown: _apply_vehicle_breakdown,
    EventType.new_urgent_task: _apply_new_urgent_task,
    EventType.road_closure: _apply_road_closure,
    EventType.vehicle_delay: _apply_vehicle_delay,
    EventType.priority_changed: _apply_priority_changed,
    EventType.destination_changed: _apply_destination_changed,
    EventType.equipment_request: _apply_equipment_request,
}
