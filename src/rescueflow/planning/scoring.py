"""Lexicographic objective scoring shared by B0 and A1 (docs/CONTRACTS.md 6.4).

All weights below are **synthetic design choices**, not calibrated clinical
risk, and are documented here rather than hidden in a single opaque number.
`ScoreBreakdown.as_tuple()` is compared lexicographically: a plan can never
buy a cheaper routine delay at the cost of one additional unserved critical
task, no matter how large `other_weighted_lateness_s` is.
"""

from __future__ import annotations

from rescueflow.models.core import PlanProposal, ScoreBreakdown, Stop, TaskPriority, WorldState

LATENESS_WEIGHTS: dict[TaskPriority, float] = {
    TaskPriority.critical: 10.0,
    TaskPriority.high: 4.0,
    TaskPriority.routine: 1.0,
}


def _soft_lateness_s(world: WorldState, stop: Stop) -> float:
    if stop.task_id is None or stop.arrival_s is None:
        return 0.0
    task = world.tasks.get(stop.task_id)
    if task is None:
        return 0.0
    deadline = task.latest_pickup_s if stop.kind.value == "pickup" else task.latest_delivery_s
    if deadline is None:
        return 0.0
    return max(0.0, stop.arrival_s - deadline)


def compute_score(world: WorldState, proposal: PlanProposal, merged: dict[str, list[Stop]]) -> ScoreBreakdown:
    unserved_critical = 0
    for task_id in proposal.unserved_task_ids:
        task = world.tasks.get(task_id)
        if task is not None and task.priority == TaskPriority.critical:
            unserved_critical += 1

    critical_tardiness_s = 0.0
    other_weighted_lateness_s = 0.0
    for stops in merged.values():
        for stop in stops:
            if stop.task_id is None:
                continue
            task = world.tasks.get(stop.task_id)
            if task is None or task.id in proposal.unserved_task_ids:
                continue
            lateness = _soft_lateness_s(world, stop)
            if lateness <= 0:
                continue
            if task.priority == TaskPriority.critical:
                critical_tardiness_s += lateness
            else:
                other_weighted_lateness_s += lateness * LATENESS_WEIGHTS[task.priority]

    assignment_churn = 0
    for task_id in proposal.changed_task_ids:
        task = world.tasks.get(task_id)
        if task is not None and task.assigned_vehicle_id is not None:
            assignment_churn += 1

    extra_travel_s = 0.0
    existing_by_vehicle = {vid: v.route_stops for vid, v in world.vehicles.items()}
    for vp in proposal.vehicle_plans:
        new_stops = vp.stops
        old_stops = existing_by_vehicle.get(vp.vehicle_id, [])
        new_span = _route_span(new_stops)
        old_span = _route_span(old_stops)
        extra_travel_s += max(0.0, new_span - old_span)

    return ScoreBreakdown(
        unserved_critical=unserved_critical,
        critical_tardiness_s=critical_tardiness_s,
        other_weighted_lateness_s=other_weighted_lateness_s,
        assignment_churn=assignment_churn,
        extra_travel_s=extra_travel_s,
    )


def _route_span(stops: list[Stop]) -> float:
    if not stops:
        return 0.0
    first = stops[0].arrival_s or 0.0
    last = stops[-1].departure_s or stops[-1].arrival_s or 0.0
    return max(0.0, last - first)
