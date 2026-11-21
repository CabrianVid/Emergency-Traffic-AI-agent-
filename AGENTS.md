# AGENTS.md -- onboarding for the next coding agent (prompt 2/4 onward)

You are continuing work on RescueFlow AI, an AdaHack 2026 hackathon
demonstration. **Before writing any code:**

1. Read [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) -- the
   source-of-truth design document (practitioner-interview-informed v3
   plan). It is long; the parts most relevant to prompt 2 are sections 7
   (SUMO simulation design), 8 (green-wave subsystem) and 9 (AI agent/
   approval model).
2. Read [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) --
   exactly what prompt 1 built, what's stubbed/`NotImplementedError`, known
   limitations, and the recommended next prompt. Do not assume anything
   works until this file (or the actual code) says so.
3. Read [docs/CONTRACTS.md](docs/CONTRACTS.md) -- the invariants every
   planner/validator/approval change must continue to satisfy, and *why*
   (e.g. `plan_revision` vs `telemetry_seq`, why candidate planning must
   never mutate committed state).
4. Skim [docs/DECISIONS.md](docs/DECISIONS.md) for simplifications already
   made on purpose (e.g. the pickup+dropoff-as-consecutive-pair insertion
   model, the "breakdown location = vehicle's last confirmed node"
   approximation) so you don't "fix" something that was a deliberate
   hackathon-scoped tradeoff -- or, if you do change it, update that file
   with the new reasoning.

## Ground rules (carried forward from prompt 1)

- **Don't call TraCI from anywhere except `rescueflow.sumo.controller`.**
  Planning and the approval gate operate on `WorldState`/`PlanProposal`
  only; `TraciSimulationController` is the single owner of the TraCI
  connection (docs/CONTRACTS.md "Ownership and process layout").
- **Never mutate committed `WorldState` while generating a candidate plan.**
  Only `rescueflow.approval.service.DefaultApprovalService.approve()` and
  `rescueflow.events.apply.apply_event()` are allowed to do that, and both
  re-validate before doing so.
- **Every `Planner`/`PlanValidator`/`ApprovalService` change must still
  pass `tests/test_validator_invariants.py` and `tests/test_approval.py`
  unchanged.** If a documented requirement genuinely changes a test's
  premise, update the test *and* record why in `docs/DECISIONS.md` -- don't
  silently weaken an assertion to make a broken implementation pass.
- Run `rescueflow doctor` first in a new environment; it tells you exactly
  what's missing and how to fix it (never assume SUMO "just works").
- Commit in small, meaningful checkpoints (2-4 for a prompt-sized unit of
  work), not one giant commit at the end.
- `docs/IMPLEMENTATION_STATUS.md` is the single place that must stay
  accurate after your change -- update it before you report done.

## What's next (prompt 2 scope, per docs/DEVELOPMENT_PLAN.md)

See "Recommended next prompt" at the bottom of
`docs/IMPLEMENTATION_STATUS.md` for the concrete, scoped list (SUMO
execution loop / `apply_committed_plan`, the remaining disruption event
types, OR-Tools stage-2 optimizer, green-wave signal priority).
