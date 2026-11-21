# Contracts

The canonical types live in `src/rescueflow/models/core.py`
(`schema_version = "1.0.0"`); the module docstrings there are the first
source of truth. This file explains the *invariants* -- what must stay true
across any future change -- and points at the test that enforces each one.

## `plan_revision` vs `telemetry_seq`

`WorldState.plan_revision` increments only on a planning-affecting change:
applying a validated event (`events.apply.apply_event`) or committing an
approved proposal (`approval.service.DefaultApprovalService.approve`).
`WorldState.telemetry_seq` is a separate counter for frequent simulator
position updates (bumped by `SimulationController.step`/`bump_telemetry`)
and **must never** be used to decide whether a `PlanProposal` is stale.

A proposal records `based_on_plan_revision`. Approval rejects a proposal
whose `based_on_plan_revision != world.plan_revision` ("stale: plan has
moved on") -- see `test_approval.py::test_stale_revision_is_rejected_not_committed`.
A proposal also carries a `ttl_s` (default 120s simulated); approval
rejects it past `proposed_at_sim_s + ttl_s` -- see
`test_approval.py::test_ttl_expiry_is_rejected`. Both checks run against
`world.plan_revision`/`world.sim_time_s`, never `telemetry_seq`.

## Pure candidate planning

`BaselinePlanner.baseline_repair` and `OptimizedPlanner.optimized_repair`
never mutate the `WorldState` they are given -- they work on
`vehicle.model_copy(deep=True)` copies internally and return a
`PlanProposal` (which records `based_on_plan_revision`, the changed/
unserved task ids, the proposed per-vehicle stops with estimated times, a
`FeasibilityReport`, and a `ScoreBreakdown` with a human-readable
`explanation`). The only two places allowed to write to committed
`WorldState` are `events.apply.apply_event` (committing an *observed fact*)
and `approval.service.DefaultApprovalService.approve` (committing an
*operator-approved assignment*), and both re-validate immediately before
writing.

## Idempotent, revalidated approval

`DefaultApprovalService.approve(world, proposal, approved_by, idempotency_key)`
checks, in order: not already committed; `based_on_plan_revision` matches;
TTL; a fresh `DefaultPlanValidator.validate()` call (the world may have
changed since the proposal was computed). Only if all four pass does it
write `vehicle.route_stops`, update task status/assignment, bump
`plan_revision`, and call the `DispatchNotificationAdapter`. Calling
`approve()` twice with the same `idempotency_key` returns the cached first
`CommitResult` without re-running any of this -- see
`test_approval.py::test_double_approve_with_same_idempotency_key_does_not_double_apply`.

## Operator/safety invariants (`DefaultPlanValidator`)

Each of these is one check in `planning/validator.py` and one test in
`tests/test_validator_invariants.py`:

| Invariant | Check | Test |
|---|---|---|
| No vehicle overlap / valid pickup-before-dropoff | `_check_capacity_and_ordering` | (exercised indirectly by every planner test) |
| Medical/crew capability matching | `_check_eligibility` (`missing_capability`) | `test_no_incompatible_als_assignment_rejected` |
| Patient/equipment capacity never exceeded | `_check_capacity_and_ordering` | (exercised indirectly) |
| Asset physically at its claimed pickup location (no teleporting K1) | `_check_asset_custody` (`asset_not_at_pickup`) | `test_k1_cannot_be_delivered_before_recovery_and_custody_transfer` |
| Locked/in-progress leg never reordered or removed | `_check_locked_prefix_unchanged` (`locked_stop_reassigned`) | `test_locked_carrying_patient_leg_cannot_be_reassigned` |
| Hospital receiving-slot capacity | `_check_receiving_capacity` (`receiving_capacity_exceeded`) | `test_hospital_receiving_capacity_enforced` |
| Broken/grounded vehicle gets no new stops | `_check_grounded_or_broken` (`unavailable_vehicle_assigned`) | `test_grounded_helicopter_cannot_receive_new_stops` |
| Hard time windows | `_check_time_windows` (`hard_deadline_violated`) | (soft windows instead feed `ScoreBreakdown` lateness, see `planning/scoring.py`) |

A subtlety worth preserving: the locked-prefix and broken/grounded checks
only run against vehicles a `PlanProposal` actually includes in
`vehicle_plans` (see `merged_vehicle_stops` / `touched_vehicle_ids` in
`validator.py`). An untouched vehicle's *pre-existing* stops (e.g. the
leftover, already-executed pickup stop still sitting in a just-broken
vehicle's history) are not something a given validation pass is being
asked to approve, and must not fail it.

## Occupied-ambulance breakdown must escalate, never invent a handoff

`events.apply._apply_vehicle_breakdown` checks, per affected task, whether
a *patient* (not just equipment) is physically aboard
(`patient_slots > 0 and status in (picked_up, en_route_dropoff)`). If so it
sets `status = requires_operator_intervention` and does **not** create a
recovery task or touch `assigned_vehicle_id` -- see
`test_events.py::test_occupied_ambulance_breakdown_escalates_instead_of_inventing_handoff`.
Only an equipment-only interrupted leg gets an automatic
`equipment_recovery` task.

## Event validation never overwrites a conflicting observation

`EventInterpreter.validate_event` checks a reported fact (e.g. "K1 is
aboard R2") against `WorldState.assets[...]location` before building an
`Event`. A mismatch returns a `ValidationError` (code
`asset_location_conflict`); the world is never silently overwritten to
match the report -- see
`test_events.py::test_reported_asset_location_conflict_is_flagged_not_overwritten`.
Free text that cannot be confidently classified raises `EventParseError`
rather than guessing a type -- see
`test_events.py::test_unrecognized_free_text_raises_instead_of_guessing`.
