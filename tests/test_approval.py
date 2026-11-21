"""Required invariant test (6): stale plan approval / idempotency.
docs/CONTRACTS.md 9.4: a proposal's `based_on_plan_revision` must match the
*current* planning revision, a TTL applies, and approving the same
idempotency key twice must not double-apply."""

from __future__ import annotations

from rescueflow.approval.service import DefaultApprovalService
from rescueflow.models.core import (
    FeasibilityReport,
    PlanProposal,
    ScoreBreakdown,
    Stop,
    StopKind,
    Task,
    TaskKind,
    TaskPriority,
    Vehicle,
    VehicleMode,
    VehiclePlan,
    WorldState,
)


def _simple_world() -> WorldState:
    task = Task(id="T1", kind=TaskKind.patient_transport, priority=TaskPriority.high,
                pickup_node="A", dropoff_node="B", created_at_s=0.0, patient_slots=1)
    vehicle = Vehicle(id="V1", mode=VehicleMode.ambulance, node_id="A", patient_capacity=1)
    return WorldState(vehicles={"V1": vehicle}, tasks={"T1": task}, plan_revision=5, sim_time_s=100.0)


def _proposal_for(world: WorldState, ttl_s: float = 60.0) -> PlanProposal:
    stops = [
        Stop(stop_id="s1", vehicle_id="V1", task_id="T1", kind=StopKind.pickup, node_id="A", arrival_s=100),
        Stop(stop_id="s2", vehicle_id="V1", task_id="T1", kind=StopKind.dropoff, node_id="B", arrival_s=150),
    ]
    return PlanProposal(
        proposal_id="p1", based_on_plan_revision=world.plan_revision, proposed_at_sim_s=world.sim_time_s,
        policy="b0", vehicle_plans=[VehiclePlan(vehicle_id="V1", stops=stops)],
        feasibility=FeasibilityReport(feasible=True), score=ScoreBreakdown(), ttl_s=ttl_s,
    )


def test_stale_revision_is_rejected_not_committed():
    world = _simple_world()
    proposal = _proposal_for(world)
    world.bump_plan_revision()  # something else changed the world in the meantime

    result = DefaultApprovalService().approve(world, proposal, approved_by="dispatcher", idempotency_key="k1")

    assert result.committed is False
    assert "stale" in result.rejection_reason
    assert proposal.committed is False


def test_ttl_expiry_is_rejected():
    world = _simple_world()
    proposal = _proposal_for(world, ttl_s=10.0)
    world.sim_time_s += 999  # long after the TTL

    result = DefaultApprovalService().approve(world, proposal, approved_by="dispatcher", idempotency_key="k1")

    assert result.committed is False
    assert "TTL" in result.rejection_reason


def test_double_approve_with_same_idempotency_key_does_not_double_apply():
    world = _simple_world()
    proposal = _proposal_for(world)
    service = DefaultApprovalService()

    first = service.approve(world, proposal, approved_by="dispatcher", idempotency_key="same-key")
    revision_after_first = world.plan_revision
    assert first.committed is True

    second = service.approve(world, proposal, approved_by="dispatcher", idempotency_key="same-key")

    assert second is first  # cached result, not recomputed
    assert world.plan_revision == revision_after_first  # not bumped again
    assert len(world.decisions) == 1


def test_approving_an_already_committed_proposal_under_a_new_key_is_rejected():
    world = _simple_world()
    proposal = _proposal_for(world)
    service = DefaultApprovalService()
    service.approve(world, proposal, approved_by="dispatcher", idempotency_key="k1")

    result = service.approve(world, proposal, approved_by="dispatcher", idempotency_key="different-key")

    assert result.committed is False
    assert "already committed" in result.rejection_reason
