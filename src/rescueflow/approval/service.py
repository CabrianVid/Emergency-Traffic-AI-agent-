"""Approval gate (docs/CONTRACTS.md 9.4): the only place a `PlanProposal` is
allowed to mutate committed `WorldState`.

Checks, in order: not already committed: revision freshness
(`based_on_plan_revision == world.plan_revision` -- a *semantic* planning
revision, never telemetry), TTL, and a fresh feasibility re-check (the world
may have moved on since the proposal was generated). Approving the same
`idempotency_key` twice returns the first result unchanged rather than
double-applying.
"""

from __future__ import annotations

import uuid

from rescueflow.interfaces.protocols import ApprovalRequest, CommitResult
from rescueflow.models.core import DispatchMessage, OperatorDecision, PlanProposal, TaskStatus, WorldState
from rescueflow.planning.validator import DefaultPlanValidator


class DefaultApprovalService:
    """Concrete `ApprovalService`."""

    def __init__(self, validator: DefaultPlanValidator | None = None, notifier=None) -> None:
        self.validator = validator or DefaultPlanValidator()
        self.notifier = notifier
        self._idempotency_cache: dict[str, CommitResult] = {}

    def request_approval(self, proposal: PlanProposal) -> ApprovalRequest:
        return ApprovalRequest(
            proposal_id=proposal.proposal_id,
            based_on_plan_revision=proposal.based_on_plan_revision,
            expires_at_sim_s=proposal.proposed_at_sim_s + proposal.ttl_s,
            summary=proposal.explanation,
        )

    def approve(
        self, world: WorldState, proposal: PlanProposal, approved_by: str, idempotency_key: str
    ) -> CommitResult:
        if idempotency_key in self._idempotency_cache:
            return self._idempotency_cache[idempotency_key]

        if proposal.committed:
            result = CommitResult(proposal_id=proposal.proposal_id, committed=False, rejection_reason="already committed")
            self._idempotency_cache[idempotency_key] = result
            return result

        if proposal.based_on_plan_revision != world.plan_revision:
            result = CommitResult(
                proposal_id=proposal.proposal_id, committed=False,
                rejection_reason=(
                    f"stale proposal: based on plan_revision {proposal.based_on_plan_revision}, "
                    f"world is now at {world.plan_revision}"
                ),
            )
            self._idempotency_cache[idempotency_key] = result
            return result

        if world.sim_time_s > proposal.proposed_at_sim_s + proposal.ttl_s:
            result = CommitResult(
                proposal_id=proposal.proposal_id, committed=False,
                rejection_reason=f"stale proposal: TTL of {proposal.ttl_s}s expired",
            )
            self._idempotency_cache[idempotency_key] = result
            return result

        feasibility = self.validator.validate(world, proposal)
        if not feasibility.feasible:
            result = CommitResult(
                proposal_id=proposal.proposal_id, committed=False,
                rejection_reason="failed fresh feasibility re-check: "
                + "; ".join(i.message for i in feasibility.issues),
            )
            self._idempotency_cache[idempotency_key] = result
            return result

        dispatch_messages: list[DispatchMessage] = []
        for vp in proposal.vehicle_plans:
            world.vehicles[vp.vehicle_id].route_stops = vp.stops
            seen_tasks: set[str] = set()
            for stop in vp.stops:
                if stop.task_id is None or stop.task_id in seen_tasks:
                    continue
                seen_tasks.add(stop.task_id)
                task = world.tasks.get(stop.task_id)
                if task is None:
                    continue
                previously_assigned = task.assigned_vehicle_id
                task.assigned_vehicle_id = vp.vehicle_id
                if task.status in (TaskStatus.pending, TaskStatus.unserved):
                    task.status = TaskStatus.planned
                if previously_assigned != vp.vehicle_id and self.notifier is not None:
                    dest_task = world.tasks[stop.task_id]
                    msg = self.notifier.notify(
                        world, vp.vehicle_id, stop.task_id, dest_task.dropoff_node, world.plan_revision
                    )
                    dispatch_messages.append(msg)

        for task_id in proposal.unserved_task_ids:
            task = world.tasks.get(task_id)
            if task is not None and task.status not in (TaskStatus.requires_operator_intervention,):
                task.status = TaskStatus.unserved

        proposal.committed = True
        world.bump_plan_revision()

        decision = OperatorDecision(
            decision_id=f"dec-{uuid.uuid4().hex[:10]}",
            proposal_id=proposal.proposal_id,
            approved_by=approved_by,
            decision_at_sim_s=world.sim_time_s,
            outcome="approved",
            idempotency_key=idempotency_key,
        )
        world.decisions.append(decision)

        result = CommitResult(
            proposal_id=proposal.proposal_id, committed=True, new_plan_revision=world.plan_revision,
            decision=decision, dispatch_messages=dispatch_messages,
        )
        self._idempotency_cache[idempotency_key] = result
        return result
