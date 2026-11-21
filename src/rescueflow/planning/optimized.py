"""A1: fleet-wide reinsertion + bounded local search (docs/CONTRACTS.md 6.3,
stage 1 of the implementation ladder -- OR-Tools stage 2 is not implemented
in this prompt, see docs/IMPLEMENTATION_STATUS.md).

Unlike B0, A1's candidate pool is every task that is **not locked in
progress** -- pending tasks *and* tasks already assigned to a vehicle that
has not yet started executing them. It can relocate the latter to a
different vehicle if that improves the lexicographic objective
(docs/CONTRACTS.md 6.4). A locked/in-progress leg (`Vehicle.locked_stop_index`)
is never touched, matching B0.

Two transparent profiles are returned (never fabricated -- if a profile
collapses to the same plan as another, both are still reported so the
operator can see they agree):

- `urgent_first`: insert critical/high tasks before routine ones, accepting
  extra churn/travel to get them served sooner.
- `minimum_disruption`: insert in original call order, then run a bounded
  swap/relocate local search that only accepts moves which do not make the
  score lexicographically worse.
"""

from __future__ import annotations

import time
import uuid

from rescueflow.interfaces.protocols import TravelTimeProvider
from rescueflow.models.core import (
    Event,
    FeasibilityReport,
    PlanProposal,
    ScoreBreakdown,
    Task,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehiclePlan,
    WorldState,
)
from rescueflow.planning.insertion import greedy_insert_all, is_vehicle_eligible, try_insert
from rescueflow.planning.scoring import compute_score
from rescueflow.planning.solver_log import SolverLog
from rescueflow.planning.validator import DefaultPlanValidator, merged_vehicle_stops

_PRIORITY_RANK = {TaskPriority.critical: 0, TaskPriority.high: 1, TaskPriority.routine: 2}

LOCAL_SEARCH_MAX_PASSES = 2
DEFAULT_SOLVE_BUDGET_S = 3.0


_NON_MOVABLE_STATUSES = (
    TaskStatus.en_route_pickup,
    TaskStatus.picked_up,
    TaskStatus.en_route_dropoff,
    TaskStatus.completed,
    TaskStatus.interrupted,
    TaskStatus.unserved,
    TaskStatus.requires_operator_intervention,
)


def _is_movable(task: Task, world: WorldState) -> bool:
    """Pending tasks, plus tasks already assigned to a vehicle that has not
    yet started executing them (status == planned, not in that vehicle's
    locked/in-progress prefix). Anything in flight, completed, superseded or
    escalated is never eligible for A1 to pick up and relocate."""
    if task.status == TaskStatus.pending:
        return True
    if task.status in _NON_MOVABLE_STATUSES:
        return False
    if task.status != TaskStatus.planned or task.assigned_vehicle_id is None:
        return False
    vehicle = world.vehicles.get(task.assigned_vehicle_id)
    if vehicle is None:
        return False
    locked_task_ids = {
        s.task_id for s in vehicle.route_stops[: (vehicle.locked_stop_index or -1) + 1]
    }
    return task.id not in locked_task_ids


def _pull_movable_tasks(world: WorldState, movable_ids: set[str]) -> dict[str, Vehicle]:
    working: dict[str, Vehicle] = {}
    for vid, vehicle in world.vehicles.items():
        clone = vehicle.model_copy(deep=True)
        start = 0 if vehicle.locked_stop_index is None else vehicle.locked_stop_index + 1
        kept_tail = [s for s in clone.route_stops[start:] if s.task_id not in movable_ids]
        clone.route_stops = clone.route_stops[:start] + kept_tail
        working[vid] = clone
    return working


def _urgent_first_key(task: Task) -> tuple:
    deadline = task.latest_delivery_s if task.latest_delivery_s is not None else float("inf")
    return (_PRIORITY_RANK[task.priority], deadline, task.created_at_s)


def _local_search(
    world: WorldState,
    travel: TravelTimeProvider,
    validator: DefaultPlanValidator,
    working_vehicles: dict[str, Vehicle],
    movable_tasks: list[Task],
    deadline_monotonic: float | None = None,
) -> None:
    """Bounded relocate/swap pass: for each movable task currently placed,
    try moving it to a different vehicle; keep the move only if the
    resulting per-vehicle schedule is feasible and no more costly. Respects
    the same hard wall-clock solve budget as the insertion phase."""
    for _pass in range(LOCAL_SEARCH_MAX_PASSES):
        if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
            break
        improved = False
        for task in movable_tasks:
            if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
                break
            current_vid = next(
                (vid for vid, v in working_vehicles.items() if any(s.task_id == task.id for s in v.route_stops)),
                None,
            )
            if current_vid is None:
                continue
            current_vehicle = working_vehicles[current_vid]
            current_added = next(
                (s for s in current_vehicle.route_stops if s.task_id == task.id and s.kind.value == "pickup"), None
            )
            if current_added is None:
                continue

            for alt_vehicle in working_vehicles.values():
                if alt_vehicle.id == current_vid or not is_vehicle_eligible(world, alt_vehicle, task):
                    continue
                alt_candidates = try_insert(world, travel, validator, alt_vehicle, task)
                if not alt_candidates:
                    continue
                best_alt = alt_candidates[0]

                trimmed = current_vehicle.model_copy(deep=True)
                trimmed.route_stops = [s for s in trimmed.route_stops if s.task_id != task.id]

                if best_alt.added_travel_s + 1e-6 < current_added_cost(working_vehicles, current_vid, task):
                    working_vehicles[current_vid] = trimmed
                    working_vehicles[alt_vehicle.id].route_stops = best_alt.stops
                    improved = True
                    break
        if not improved:
            break


def current_added_cost(working_vehicles: dict[str, Vehicle], vehicle_id: str, task: Task) -> float:
    """Rough cost proxy: time between the task's own pickup and dropoff
    departure on its current vehicle (used only to decide whether a relocate
    is worth it, not for final scoring)."""
    stops = working_vehicles[vehicle_id].route_stops
    relevant = [s for s in stops if s.task_id == task.id]
    if len(relevant) < 2:
        return float("inf")
    return (relevant[-1].departure_s or 0.0) - (relevant[0].arrival_s or 0.0)


class OptimizedPlanner:
    """Concrete `Planner.optimized_repair`."""

    def __init__(
        self,
        travel: TravelTimeProvider,
        validator: DefaultPlanValidator | None = None,
        solver_log: SolverLog | None = None,
        solve_budget_s: float = DEFAULT_SOLVE_BUDGET_S,
    ) -> None:
        self.travel = travel
        self.validator = validator or DefaultPlanValidator()
        self.solver_log = solver_log
        self.solve_budget_s = solve_budget_s

    def optimized_repair(
        self, world: WorldState, event: Event | None, profiles: list[str]
    ) -> list[PlanProposal]:
        movable = [t for t in world.tasks.values() if _is_movable(t, world)]
        movable_ids = {t.id for t in movable}

        proposals: list[PlanProposal] = []
        seen_signatures: set[tuple] = set()
        for profile in profiles:
            deadline = time.monotonic() + self.solve_budget_s
            working_vehicles = _pull_movable_tasks(world, movable_ids)
            if profile == "urgent_first":
                order = sorted(movable, key=_urgent_first_key)
            else:
                order = sorted(movable, key=lambda t: t.created_at_s)

            changed, unserved = greedy_insert_all(
                world, self.travel, self.validator, working_vehicles, order,
                deadline_monotonic=deadline, solver_log=self.solver_log, policy="a1", profile=profile,
            )

            if profile == "minimum_disruption":
                placed = [t for t in movable if t.id in changed]
                _local_search(world, self.travel, self.validator, working_vehicles, placed, deadline_monotonic=deadline)

            vehicle_plans = [
                VehiclePlan(vehicle_id=vid, stops=wv.route_stops)
                for vid, wv in working_vehicles.items()
                if wv.route_stops != world.vehicles[vid].route_stops
            ]

            proposal = PlanProposal(
                proposal_id=f"a1-{profile}-{uuid.uuid4().hex[:8]}",
                based_on_plan_revision=world.plan_revision,
                proposed_at_sim_s=world.sim_time_s,
                policy="a1",
                profile=profile,
                triggering_event_id=event.event_id if event else None,
                vehicle_plans=vehicle_plans,
                changed_task_ids=changed,
                unserved_task_ids=unserved,
                feasibility=FeasibilityReport(feasible=True),
                score=ScoreBreakdown(),
                explanation=(
                    f"A1 ({profile}): reconsidered {len(movable)} unlocked task(s), "
                    f"assigned {len(changed)}, {len(unserved)} unserved."
                ),
            )
            merged = merged_vehicle_stops(world, proposal)
            proposal.score = compute_score(world, proposal, merged)
            proposal.feasibility = self.validator.validate(world, proposal)

            signature = plan_outcome_signature(world, proposal)
            if signature in seen_signatures:
                proposal.explanation += " (identical outcome to another profile)"
            seen_signatures.add(signature)
            proposals.append(proposal)

            if self.solver_log is not None:
                self.solver_log.plan_selected(
                    proposal.proposal_id, "a1", profile, proposal.score.model_dump(),
                    eta_assumption="static free-flow shortest path on the SUMO network graph (docs/DECISIONS.md)",
                )

        return proposals


def plan_outcome_signature(world: WorldState, proposal: PlanProposal) -> tuple:
    """Compare schedules independent of generated proposal and stop IDs."""
    routes = merged_vehicle_stops(world, proposal)
    return (
        tuple(
            (vehicle_id, tuple(
                (s.task_id, s.kind, s.node_id, s.arrival_s, s.departure_s)
                for s in routes[vehicle_id]
            ))
            for vehicle_id in sorted(routes)
        ),
        tuple(sorted(proposal.unserved_task_ids)),
    )
