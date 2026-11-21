"""B0: credible observed-workflow-inspired conventional repair baseline.

Not a claim about the actual Slovenian dispatch algorithm -- see
docs/DECISIONS.md. Behavior (docs/CONTRACTS.md 6.2):

1. Collect pending/newly-affected tasks, sort by priority, latest deadline,
   call time.
2. For each, in order, find the single nearest available/eligible/feasible
   vehicle (cheapest feasible insertion across the whole fleet) and commit it
   immediately to a working copy of the plan before moving to the next task.
3. Leave every unaffected committed assignment untouched.
4. A task with no feasible vehicle anywhere becomes unserved, never
   hallucinated onto an ineligible vehicle.
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
    VehiclePlan,
    WorldState,
)
from rescueflow.planning.insertion import greedy_insert_all
from rescueflow.planning.scoring import compute_score
from rescueflow.planning.solver_log import SolverLog
from rescueflow.planning.validator import DefaultPlanValidator, merged_vehicle_stops

DEFAULT_SOLVE_BUDGET_S = 2.0

_PRIORITY_RANK = {TaskPriority.critical: 0, TaskPriority.high: 1, TaskPriority.routine: 2}


def _sort_key(task: Task) -> tuple:
    deadline = task.latest_delivery_s if task.latest_delivery_s is not None else float("inf")
    return (_PRIORITY_RANK[task.priority], deadline, task.created_at_s)


class BaselinePlanner:
    """Concrete `Planner.baseline_repair`."""

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

    def baseline_repair(
        self, world: WorldState, event: Event | None, profile: str = "minimum_disruption"
    ) -> PlanProposal:
        pending = [t for t in world.tasks.values() if t.status == TaskStatus.pending]
        pending.sort(key=_sort_key)

        working_vehicles = {vid: v.model_copy(deep=True) for vid, v in world.vehicles.items()}
        deadline = time.monotonic() + self.solve_budget_s
        changed_task_ids, unserved_task_ids = greedy_insert_all(
            world, self.travel, self.validator, working_vehicles, pending,
            deadline_monotonic=deadline, solver_log=self.solver_log, policy="b0", profile=profile,
        )

        vehicle_plans = [
            VehiclePlan(vehicle_id=vid, stops=wv.route_stops)
            for vid, wv in working_vehicles.items()
            if wv.route_stops != world.vehicles[vid].route_stops
        ]

        proposal = PlanProposal(
            proposal_id=f"b0-{uuid.uuid4().hex[:8]}",
            based_on_plan_revision=world.plan_revision,
            proposed_at_sim_s=world.sim_time_s,
            policy="b0",
            profile="minimum_disruption",
            triggering_event_id=event.event_id if event else None,
            vehicle_plans=vehicle_plans,
            changed_task_ids=changed_task_ids,
            unserved_task_ids=unserved_task_ids,
            feasibility=FeasibilityReport(feasible=True),
            score=ScoreBreakdown(),
            explanation=(
                f"B0 greedy nearest-eligible repair: assigned {len(changed_task_ids)} task(s), "
                f"{len(unserved_task_ids)} unserved."
            ),
        )
        merged = merged_vehicle_stops(world, proposal)
        proposal.score = compute_score(world, proposal, merged)
        proposal.feasibility = self.validator.validate(world, proposal)
        if self.solver_log is not None:
            self.solver_log.plan_selected(
                proposal.proposal_id, "b0", proposal.profile, proposal.score.model_dump(),
                eta_assumption="static free-flow shortest path on the SUMO network graph (docs/DECISIONS.md)",
            )
        return proposal
