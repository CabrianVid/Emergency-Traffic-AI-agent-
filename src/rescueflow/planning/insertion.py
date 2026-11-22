"""Shared insertion/scheduling helpers for B0 and A1.

Both planners only ever insert a task's pickup+dropoff as a **consecutive
pair** into the unlocked tail of a vehicle's stop list (no interleaving of a
third task between one task's own pickup and dropoff). That is a documented
simplification of the general pickup-and-delivery VRP -- see
docs/DECISIONS.md -- acceptable for the hackathon's small fleet, and it keeps
every schedule trivially easy to validate.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass

from rescueflow.interfaces.protocols import TravelTimeProvider
from rescueflow.models.core import (
    FeasibilityReport,
    Stop,
    StopKind,
    Task,
    TaskKind,
    TaskPriority,
    Vehicle,
    VehicleMode,
    VehicleStatus,
    WorldState,
)
from rescueflow.planning.validator import DefaultPlanValidator
from rescueflow.models.core import PlanProposal, VehiclePlan, ScoreBreakdown

_stop_counter = itertools.count(1)
_PRIORITY_RANK = {TaskPriority.critical: 0, TaskPriority.high: 1, TaskPriority.routine: 2}


def next_stop_id(vehicle_id: str) -> str:
    return f"{vehicle_id}-stop-{next(_stop_counter)}"


def is_vehicle_eligible(world: WorldState, vehicle: Vehicle, task: Task) -> bool:
    if vehicle.status in (VehicleStatus.broken, VehicleStatus.weather_grounded, VehicleStatus.offline):
        return False
    if vehicle.mode not in task.allowed_modes:
        return False
    if set(task.required_capabilities) - set(vehicle.capabilities):
        return False
    if task.patient_slots > vehicle.patient_capacity:
        return False
    if task.equipment_units > vehicle.equipment_capacity_units:
        return False
    for asset_id in task.required_asset_ids:
        if asset_id not in world.assets:
            return False
    # Physical custody (is the asset actually reachable at the pickup node
    # by this vehicle?) is checked per-insertion by the validator, not here.
    return True


def _eta_s(travel: TravelTimeProvider, mode: VehicleMode, origin: str, dest: str, depart_s: float) -> float:
    if mode == VehicleMode.helicopter:
        return travel.air_eta_s(origin, dest)
    return travel.road_eta_s(origin, dest, depart_s)


def _unlocked_slice(vehicle: Vehicle) -> tuple[list[Stop], int]:
    start = 0 if vehicle.locked_stop_index is None else vehicle.locked_stop_index + 1
    return list(vehicle.route_stops[start:]), start


def _node_and_time_before(vehicle: Vehicle, prefix_and_unlocked: list[Stop], index: int) -> tuple[str, float]:
    """Node and time the vehicle is free at, right before inserting at `index`
    into `prefix_and_unlocked` (the full stop list including the locked
    prefix, so index is an absolute position)."""
    if index == 0:
        return vehicle.node_id, vehicle.available_after_s
    prev = prefix_and_unlocked[index - 1]
    return prev.node_id, prev.departure_s if prev.departure_s is not None else vehicle.available_after_s


def _hospital_handling_s(world: WorldState, node_id: str) -> float:
    for site in world.receiving_sites.values():
        if site.node_id == node_id:
            return site.service_handling_s
    return 0.0


@dataclass
class InsertionCandidate:
    vehicle_id: str
    stops: list[Stop]
    """Full new stop list for the vehicle (locked prefix + everything after)."""
    added_travel_s: float
    insertion_index: int


def recompute_tail(
    world: WorldState,
    travel: TravelTimeProvider,
    vehicle: Vehicle,
    stops: list[Stop],
    from_index: int,
) -> None:
    """Recompute arrival/service/departure times in-place for stops[from_index:],
    given stops[:from_index] already has correct times (or from_index == 0,
    in which case we start from the vehicle's current free node/time)."""
    node, time_s = _node_and_time_before(vehicle, stops, from_index)
    time_s = max(time_s, world.sim_time_s)
    for stop in stops[from_index:]:
        travel_s = _eta_s(travel, vehicle.mode, node, stop.node_id, time_s)
        arrival = time_s + travel_s
        task = world.tasks.get(stop.task_id) if stop.task_id else None
        if task is not None:
            if stop.kind == StopKind.pickup and task.earliest_pickup_s is not None:
                arrival = max(arrival, task.earliest_pickup_s)
            if stop.kind == StopKind.dropoff and task.earliest_delivery_s is not None:
                arrival = max(arrival, task.earliest_delivery_s)
        service_s = 0.0
        if task is not None:
            service_s = task.load_seconds if stop.kind == StopKind.pickup else task.unload_seconds
            if stop.kind == StopKind.dropoff:
                service_s += _hospital_handling_s(world, stop.node_id)
        stop.arrival_s = arrival
        stop.service_start_s = arrival
        stop.departure_s = arrival + service_s
        node, time_s = stop.node_id, stop.departure_s


def try_insert(
    world: WorldState,
    travel: TravelTimeProvider,
    validator: DefaultPlanValidator,
    vehicle: Vehicle,
    task: Task,
    *,
    keep_priority_order: bool = False,
) -> list[InsertionCandidate]:
    """All feasible insertion points for `task` on `vehicle`, cheapest first.

    With `keep_priority_order`, `task` is never placed ahead of a pending
    pickup of a strictly more urgent task on the same vehicle (A1's
    `urgent_first` profile: routine work must not delay a critical response)."""
    unlocked, start = _unlocked_slice(vehicle)
    locked_prefix = list(vehicle.route_stops[:start])
    candidates: list[InsertionCandidate] = []
    rank = _PRIORITY_RANK[task.priority]

    for i in range(len(unlocked) + 1):
        if keep_priority_order and any(
            stop.kind == StopKind.pickup and stop.service_completed_at_s is None and stop.task_id in world.tasks
            and _PRIORITY_RANK[world.tasks[stop.task_id].priority] < rank
            for stop in unlocked[i:]
        ):
            continue
        # Fresh copies every iteration: recompute_tail mutates Stop objects
        # in place, and trial candidates (and `vehicle.route_stops` itself)
        # must never share object identity with each other across trials.
        unlocked_copy = [s.model_copy() for s in unlocked]
        locked_prefix_copy = [s.model_copy() for s in locked_prefix]
        pickup = Stop(
            stop_id=next_stop_id(vehicle.id), vehicle_id=vehicle.id, task_id=task.id,
            kind=StopKind.pickup, node_id=task.pickup_node,
            earliest_s=task.earliest_pickup_s, latest_s=task.latest_pickup_s,
        )
        dropoff = Stop(
            stop_id=next_stop_id(vehicle.id), vehicle_id=vehicle.id, task_id=task.id,
            kind=StopKind.dropoff, node_id=task.dropoff_node,
            earliest_s=task.earliest_delivery_s, latest_s=task.latest_delivery_s,
        )
        trial = locked_prefix_copy + unlocked_copy[:i] + [pickup, dropoff] + unlocked_copy[i:]
        old_span_tail_start = start + i
        try:
            recompute_tail(world, travel, vehicle, trial, old_span_tail_start)
        except ValueError:
            # A closed/disconnected road makes this insertion infeasible.
            continue

        trial_proposal = PlanProposal(
            proposal_id="trial", based_on_plan_revision=world.plan_revision, proposed_at_sim_s=world.sim_time_s,
            policy="b0", vehicle_plans=[VehiclePlan(vehicle_id=vehicle.id, stops=trial)],
            feasibility=FeasibilityReport(feasible=True), score=ScoreBreakdown(),
        )
        report = validator.validate(world, trial_proposal)
        if not report.feasible:
            continue

        old_last_departure = (
            vehicle.route_stops[-1].departure_s if vehicle.route_stops and vehicle.route_stops[-1].departure_s
            else vehicle.available_after_s
        )
        new_last_departure = trial[-1].departure_s or 0.0
        added_travel_s = max(0.0, new_last_departure - max(old_last_departure, world.sim_time_s))

        candidates.append(InsertionCandidate(vehicle_id=vehicle.id, stops=trial, added_travel_s=added_travel_s, insertion_index=i))

    candidates.sort(key=lambda c: c.added_travel_s)
    return candidates


def greedy_insert_all(
    world: WorldState,
    travel: TravelTimeProvider,
    validator: DefaultPlanValidator,
    working_vehicles: dict[str, Vehicle],
    tasks_sorted: list[Task],
    deadline_monotonic: float | None = None,
    solver_log=None,
    policy: str = "",
    profile: str = "",
    nearest_available_emergency: bool = False,
    keep_priority_order: bool = False,
) -> tuple[list[str], list[str]]:
    """Insert each task, in the given order, into the single cheapest
    feasible vehicle+position across the whole fleet, mutating
    `working_vehicles` in place. Returns (assigned_task_ids, unserved_task_ids).

    `deadline_monotonic` (a `time.monotonic()` value) is the hard wall-clock
    solve budget (docs/DEVELOPMENT_PLAN.md prompt 2 item 5): once passed,
    every remaining task is reported unserved-by-budget rather than the
    search blocking the demo indefinitely -- logged via `solver_log` if
    given, never silently."""
    assigned: list[str] = []
    unserved: list[str] = []
    for task in tasks_sorted:
        if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
            if solver_log is not None:
                solver_log.solve_budget_exhausted(policy, profile, time.monotonic(), deadline_monotonic)
            unserved.extend(t.id for t in tasks_sorted[tasks_sorted.index(task):])
            break
        best: InsertionCandidate | None = None
        best_key = None
        emergency = nearest_available_emergency and task.kind == TaskKind.urgent_response and task.priority == TaskPriority.critical
        for vehicle in working_vehicles.values():
            if not is_vehicle_eligible(world, vehicle, task):
                continue
            busy = vehicle.available_after_s > world.sim_time_s or any(
                stop.service_completed_at_s is None for stop in vehicle.route_stops
            )
            candidates = try_insert(world, travel, validator, vehicle, task, keep_priority_order=keep_priority_order)
            if emergency and busy:
                # Queue behind existing work when every suitably qualified
                # unit is committed. Never make this fallback displace it.
                candidates = [c for c in candidates if c.insertion_index == len(vehicle.route_stops)]
            if solver_log is not None:
                solver_log.candidate_evaluated(
                    policy, profile, task.id, vehicle.id, bool(candidates),
                    candidates[0].added_travel_s if candidates else None,
                )
            if candidates:
                candidate = candidates[0]
                key = (candidate.added_travel_s,)
                if emergency:
                    pickup = next(stop for stop in candidate.stops if stop.task_id == task.id and stop.kind == StopKind.pickup)
                    key = (busy, pickup.arrival_s, candidate.added_travel_s)
                if best_key is None or key < best_key:
                    best, best_key = candidate, key
        if best is None:
            unserved.append(task.id)
            if solver_log is not None:
                solver_log.candidate_rejected(policy, profile, task.id, "no eligible vehicle had a feasible insertion")
            continue
        working_vehicles[best.vehicle_id].route_stops = best.stops
        assigned.append(task.id)
    return assigned, unserved
