"""B1: a strong conventional dispatcher, without AI or fleet optimization.

One central waiting queue, served by priority and then age:

1. A call goes to the capable unit that can reach it first, if that unit is
   free. A critical call may also divert a unit whose only open work is
   routine transport that has not started.
2. A call nobody can take now *waits*; it is never parked behind one busy
   unit. The benchmark dispatches again as soon as a unit becomes ready.
3. Other new work uses cheapest insertion, never ahead of more urgent pickups.

Assignments already under way are not optimized or exchanged.
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
    StopKind,
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehiclePlan,
    WorldState,
)
from rescueflow.planning.baseline import _PRIORITY_RANK, _sort_key
from rescueflow.planning.insertion import greedy_insert_all, is_vehicle_eligible, try_insert
from rescueflow.planning.scoring import compute_score
from rescueflow.planning.validator import DefaultPlanValidator, merged_vehicle_stops

_OPEN = (TaskStatus.pending, TaskStatus.unserved)


def is_call(task: Task) -> bool:
    return task.kind == TaskKind.urgent_response or task.priority == TaskPriority.critical


def waiting_calls(world: WorldState) -> list[Task]:
    calls = [t for t in world.tasks.values() if t.status in _OPEN and is_call(t)]
    return sorted(calls, key=lambda t: (_PRIORITY_RANK[t.priority], t.created_at_s))


def is_ready(world: WorldState, vehicle: Vehicle, task: Task) -> bool:
    """Free, or (critical calls only) divertable from unstarted routine work."""
    if not is_vehicle_eligible(world, vehicle, task) or vehicle.available_after_s > world.sim_time_s:
        return False
    open_tasks = {s.task_id for s in vehicle.route_stops if s.service_completed_at_s is None}
    if not open_tasks:
        return True
    return task.priority == TaskPriority.critical and all(
        t in world.tasks
        and world.tasks[t].priority == TaskPriority.routine
        and world.tasks[t].status == TaskStatus.planned
        for t in open_tasks
    )


def ready_pairs(world: WorldState) -> frozenset[tuple[str, str]]:
    """Waiting call/unit pairs that could be dispatched at this moment."""
    return frozenset(
        (task.id, vehicle.id)
        for task in waiting_calls(world)
        for vehicle in world.vehicles.values()
        if is_ready(world, vehicle, task)
    )


class CentralQueuePlanner:
    def __init__(self, travel: TravelTimeProvider, solve_budget_s: float = 30.0) -> None:
        self.travel, self.solve_budget_s = travel, solve_budget_s
        self.validator = DefaultPlanValidator()

    def plan(self, world: WorldState, event: Event | None) -> PlanProposal:
        working = {vid: v.model_copy(deep=True) for vid, v in world.vehicles.items()}
        changed: list[str] = []
        taken: set[str] = set()
        for task in waiting_calls(world):
            best = None
            for vehicle in working.values():
                if vehicle.id in taken or not is_ready(world, vehicle, task):
                    continue
                candidates = try_insert(world, self.travel, self.validator, vehicle, task)
                if not candidates:
                    continue
                first = min(candidates, key=lambda c: c.insertion_index)
                eta = next(s.arrival_s for s in first.stops if s.task_id == task.id and s.kind == StopKind.pickup)
                if best is None or eta < best[0]:
                    best = (eta, vehicle.id, first.stops)
            if best is not None:
                working[best[1]].route_stops = best[2]
                taken.add(best[1])
                changed.append(task.id)
        rest = sorted((t for t in world.tasks.values() if t.status in _OPEN and not is_call(t)), key=_sort_key)
        assigned, unserved = greedy_insert_all(
            world, self.travel, self.validator, working, rest,
            deadline_monotonic=time.monotonic() + self.solve_budget_s, keep_priority_order=True,
        )
        proposal = PlanProposal(
            proposal_id=f"b1-{uuid.uuid4().hex[:8]}",
            based_on_plan_revision=world.plan_revision,
            proposed_at_sim_s=world.sim_time_s,
            policy="b1",
            profile="minimum_disruption",
            triggering_event_id=event.event_id if event else None,
            vehicle_plans=[
                VehiclePlan(vehicle_id=vid, stops=v.route_stops)
                for vid, v in working.items()
                if v.route_stops != world.vehicles[vid].route_stops
            ],
            changed_task_ids=changed + assigned,
            unserved_task_ids=unserved,
            feasibility=FeasibilityReport(feasible=True),
            score=ScoreBreakdown(),
            explanation=f"B1 central queue: dispatched {len(changed)} call(s).",
        )
        merged = merged_vehicle_stops(world, proposal)
        proposal.score = compute_score(world, proposal, merged)
        proposal.feasibility = self.validator.validate(world, proposal)
        return proposal
