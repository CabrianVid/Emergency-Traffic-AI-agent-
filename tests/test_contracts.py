"""Basic sanity on the canonical Pydantic contracts themselves (not
planning behavior -- see test_validator_invariants.py / test_planners.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rescueflow.models.core import (
    AssetLocation,
    FeasibilityReport,
    PlanProposal,
    ScoreBreakdown,
    Vehicle,
    VehicleMode,
    WorldState,
)


def test_world_state_rejects_mismatched_vehicle_key():
    vehicle = Vehicle(id="R1", mode=VehicleMode.ambulance, node_id="A0")
    with pytest.raises(ValidationError):
        WorldState(vehicles={"WRONG_KEY": vehicle})


def test_plan_revision_and_telemetry_seq_are_independent_counters():
    world = WorldState()
    assert world.plan_revision == 0
    assert world.telemetry_seq == 0
    world.bump_telemetry()
    world.bump_telemetry()
    assert world.telemetry_seq == 2
    assert world.plan_revision == 0  # telemetry never bumps plan_revision
    world.bump_plan_revision()
    assert world.plan_revision == 1
    assert world.telemetry_seq == 2  # and vice versa


def test_score_breakdown_lexicographic_ordering():
    cheap_but_one_more_unserved_critical = ScoreBreakdown(unserved_critical=1, extra_travel_s=0.0)
    expensive_but_fully_served = ScoreBreakdown(unserved_critical=0, extra_travel_s=10_000.0)
    assert expensive_but_fully_served.as_tuple() < cheap_but_one_more_unserved_critical.as_tuple()


def test_world_state_json_roundtrip(world):
    dumped = world.model_dump_json()
    reloaded = WorldState.model_validate_json(dumped)
    assert reloaded.vehicles.keys() == world.vehicles.keys()
    assert reloaded.tasks.keys() == world.tasks.keys()
    assert reloaded.plan_revision == world.plan_revision


def test_plan_proposal_requires_feasibility_and_score():
    with pytest.raises(ValidationError):
        PlanProposal(proposal_id="x", based_on_plan_revision=0, proposed_at_sim_s=0.0, policy="b0")  # type: ignore[call-arg]
    # Minimal valid construction should not raise.
    PlanProposal(
        proposal_id="x", based_on_plan_revision=0, proposed_at_sim_s=0.0, policy="b0",
        feasibility=FeasibilityReport(feasible=True), score=ScoreBreakdown(),
    )


def test_asset_location_kind_is_constrained():
    with pytest.raises(ValidationError):
        AssetLocation(kind="spaceship", ref_id="x")  # type: ignore[arg-type]
