"""Deterministic feasible alternatives for one decision point.

The optimizer calculates; it does not recommend. Each strategy below runs an
existing planner on a detached copy of the world. Infeasible or duplicate
schedules are dropped, so there may be fewer than three options -- options
are never invented to fill a quota. Every metric is computed from the
planners' road-network ETAs (free-flow shortest path on the SUMO graph).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rescueflow.interfaces.protocols import TravelTimeProvider
from rescueflow.models.core import (
    Event,
    PlanProposal,
    StopKind,
    TaskKind,
    TaskPriority,
    TaskStatus,
    WorldState,
)
from rescueflow.planning.baseline import BaselinePlanner
from rescueflow.planning.optimized import OptimizedPlanner, plan_outcome_signature
from rescueflow.planning.validator import merged_vehicle_stops

MAX_OPTIONS = 3
OPTION_IDS = ("A", "B", "C")

STRATEGIES = {
    "urgent_first": "Fleet-wide re-plan: most urgent work first, may reassign unstarted tasks",
    "minimum_disruption": "Fleet-wide re-plan keeping existing assignments where possible",
    "nearest_free_unit": "Conventional: nearest free unit, existing assignments untouched",
}


@dataclass
class Alternative:
    option_id: str
    strategy: str
    proposal: PlanProposal
    metrics: dict
    signal_priority_units: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return STRATEGIES[self.strategy]

    def summary(self) -> dict:
        return {
            "option_id": self.option_id, "strategy": self.strategy, "description": self.description,
            "metrics": self.metrics, "signal_priority_units": self.signal_priority_units, "warnings": self.warnings,
        }


def _planning_copy(world: WorldState, travel: TravelTimeProvider) -> WorldState:
    """Plan from each live unit's next road node (same rule as the benchmark)."""
    planning = world.model_copy(deep=True)
    travel.set_closed_edges(world.closed_edges)
    net = getattr(travel, "net", None)
    if net is not None:
        for vehicle in planning.vehicles.values():
            edge = vehicle.sumo_edge_id
            if edge and not edge.startswith(":"):
                vehicle.node_id = net.getEdge(edge).getToNode().getID()
    return planning


def _run_strategy(strategy: str, planning: WorldState, travel, event: Event | None, budget_s: float) -> PlanProposal:
    if strategy == "nearest_free_unit":
        planner = BaselinePlanner(travel, solve_budget_s=budget_s, nearest_available_emergency=True)
        return planner.baseline_repair(planning, event)
    return OptimizedPlanner(travel, solve_budget_s=budget_s).optimized_repair(planning, event, [strategy])[0]


def measure(world: WorldState, proposal: PlanProposal) -> dict:
    """Calculated consequences of committing `proposal` now (estimates)."""
    now = world.sim_time_s
    merged = merged_vehicle_stops(world, proposal)
    pickups, deliveries, assignment = {}, {}, {}
    for vehicle_id, stops in merged.items():
        for stop in stops:
            if stop.task_id is None or stop.service_completed_at_s is not None:
                continue
            if stop.kind == StopKind.pickup:
                pickups[stop.task_id] = stop.arrival_s
                assignment[stop.task_id] = vehicle_id
            elif stop.kind == StopKind.dropoff:
                deliveries[stop.task_id] = stop.arrival_s
                assignment.setdefault(stop.task_id, vehicle_id)
    open_tasks = [t for t in world.tasks.values() if t.status in (TaskStatus.pending, TaskStatus.planned)]
    critical = {t.id: pickups[t.id] - now for t in open_tasks
                if t.priority == TaskPriority.critical and pickups.get(t.id) is not None}
    misses = sorted(
        t.id for t in world.tasks.values()
        if (t.latest_pickup_s is not None and pickups.get(t.id) is not None and pickups[t.id] > t.latest_pickup_s)
        or (t.latest_delivery_s is not None and deliveries.get(t.id) is not None
            and deliveries[t.id] > t.latest_delivery_s)
    )
    equipment = {
        t.id: deliveries.get(t.id) for t in world.tasks.values()
        if t.kind in (TaskKind.equipment_delivery, TaskKind.equipment_recovery)
        and t.status not in (TaskStatus.completed, TaskStatus.interrupted)
    }
    reassigned = sorted(
        t.id for t in world.tasks.values()
        if t.assigned_vehicle_id and assignment.get(t.id) and assignment[t.id] != t.assigned_vehicle_id
    )
    unserved = list(proposal.unserved_task_ids)
    return {
        "critical_response_eta_s": {k: round(v, 1) for k, v in sorted(critical.items())},
        "worst_critical_response_eta_s": round(max(critical.values()), 1) if critical else None,
        "mean_critical_response_eta_s": round(sum(critical.values()) / len(critical), 1) if critical else None,
        "unserved_task_ids": unserved,
        "unserved_critical": proposal.score.unserved_critical,
        "estimated_deadline_misses": misses,
        "critical_tardiness_s": round(proposal.score.critical_tardiness_s, 1),
        "other_weighted_lateness_s": round(proposal.score.other_weighted_lateness_s, 1),
        "reassigned_task_ids": reassigned,
        "changed_units": sorted(vp.vehicle_id for vp in proposal.vehicle_plans),
        "equipment_delivery_eta_s": {k: (round(v - now, 1) if v is not None else None) for k, v in equipment.items()},
        "assignments": {k: assignment[k] for k in sorted(assignment)
                        if world.tasks.get(k) and world.tasks[k].status in (TaskStatus.pending, TaskStatus.planned)},
    }


def _signal_priority_units(world: WorldState, proposal: PlanProposal) -> list[str]:
    """Units whose next leg serves a critical task: eligible for green-wave priority."""
    units = []
    for vehicle_id, stops in merged_vehicle_stops(world, proposal).items():
        upcoming = next((s for s in stops if s.service_completed_at_s is None and s.task_id), None)
        task = world.tasks.get(upcoming.task_id) if upcoming else None
        if task is not None and task.priority == TaskPriority.critical:
            units.append(vehicle_id)
    return sorted(units)


def _warnings(world: WorldState, metrics: dict) -> list[str]:
    notes = []
    if metrics["unserved_task_ids"]:
        notes.append(f"Leaves {', '.join(metrics['unserved_task_ids'])} without a unit")
    if metrics["estimated_deadline_misses"]:
        notes.append(f"Estimated late: {', '.join(metrics['estimated_deadline_misses'])}")
    routine = [t for t in metrics["reassigned_task_ids"]
               if world.tasks[t].priority != TaskPriority.critical]
    if routine:
        notes.append(f"Moves existing work: {', '.join(routine)}")
    return notes


def generate_alternatives(
    world: WorldState, travel: TravelTimeProvider, event: Event | None = None, *,
    strategies: tuple[str, ...] = ("urgent_first", "minimum_disruption", "nearest_free_unit"),
    budget_s: float = 5.0, route_check=None,
) -> tuple[list[Alternative], list[str]]:
    """Up to three distinct feasible alternatives, plus notes on rejected ones.

    `route_check(world, proposal)` may raise ValueError when SUMO cannot build
    a proposal's routes; such a proposal is rejected, never shown."""
    planning = _planning_copy(world, travel)
    seen, options, notes = set(), [], []
    for strategy in strategies:
        try:
            proposal = _run_strategy(strategy, planning, travel, event, budget_s)
        except ValueError as exc:
            notes.append(f"{strategy}: {exc}")
            continue
        # Plans are computed from the next-node planning copy but committed to
        # the authoritative world revision.
        proposal.based_on_plan_revision = world.plan_revision
        if not proposal.feasibility.feasible:
            notes.extend(f"{strategy}: {issue.message}" for issue in proposal.feasibility.issues)
            continue
        signature = plan_outcome_signature(planning, proposal)
        if signature in seen:
            continue
        if route_check is not None:
            try:
                route_check(world, proposal)
            except (ValueError, KeyError) as exc:
                notes.append(f"{strategy}: {exc}")
                continue
        seen.add(signature)
        metrics = measure(planning, proposal)
        options.append(Alternative(
            option_id=OPTION_IDS[len(options)], strategy=strategy, proposal=proposal, metrics=metrics,
            signal_priority_units=_signal_priority_units(planning, proposal), warnings=_warnings(planning, metrics),
        ))
        if len(options) == MAX_OPTIONS:
            break
    return options, notes
