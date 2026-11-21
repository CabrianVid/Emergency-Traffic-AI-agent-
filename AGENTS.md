# AGENTS.md -- onboarding for the next coding agent (prompt 4/4)

You are continuing work on RescueFlow AI, an AdaHack 2026 hackathon
demonstration. **Before writing any code:**

1. Read [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) -- the
   source-of-truth design document. For prompt 4, start with sections
   10 (evaluation), 14 (verification), and 18 (build sequence).
2. Read [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) --
   exactly what prompts 1–3 built, known limitations, and
   the recommended next prompt. Do not assume anything works until this
   file (or the actual code) says so.
3. Read [docs/CONTRACTS.md](docs/CONTRACTS.md) -- the invariants every
   planner/validator/approval/controller change must continue to satisfy.
4. Read [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) -- the TraCI-level
   mechanics of the live simulation loop (save-state process model, route
   stitching, green-wave safety model, emergency-driving fairness). Prompt
   2 found and fixed several real, non-obvious bugs here; read this before
   touching `rescueflow.sumo.controller` or `rescueflow.sumo.green_wave`.
5. Skim [docs/DECISIONS.md](docs/DECISIONS.md) for simplifications already
   made on purpose, so you don't "fix" a deliberate tradeoff -- or, if you
   do change one, update that file with the new reasoning.

## Ground rules (carried forward from prompts 1-2)

- **Don't call TraCI from anywhere except `rescueflow.sumo.controller`.**
  Planning and the approval gate operate on `WorldState`/`PlanProposal`
  only; `TraciSimulationController` is the single owner of the TraCI
  connection. The dashboard holds it open on one worker thread. The CLI
  uses SUMO save-state/load-state across invocations. See
  [INTEGRATIONS.md](docs/INTEGRATIONS.md).
- **Never mutate committed `WorldState` while generating a candidate plan.**
  Only `rescueflow.approval.service.DefaultApprovalService.approve()` and
  `rescueflow.events.apply.apply_event()` are allowed to do that, and both
  re-validate before doing so.
- **A completed `Stop` (`service_completed_at_s is not None`) is history,
  never re-validated against current state** (docs/CONTRACTS.md). If you
  add a new validator check, decide explicitly whether it belongs with
  `_check_asset_custody`/`_check_time_windows` (skip completed stops) or
  `_check_capacity_and_ordering`/`_check_eligibility` (don't skip) and say
  why in the code.
- **Every `Planner`/`PlanValidator`/`ApprovalService` change must still
  pass the full existing test suite unchanged** (`pytest tests/ -v`). If a
  documented requirement genuinely changes a test's premise, update the
  test *and* record why in `docs/DECISIONS.md` -- don't silently weaken an
  assertion to make a broken implementation pass.
- **Verify SUMO-touching changes by actually running them**, not just by
  reading the code -- prompt 2's real bugs (route stitching, save-state
  timing, a circular import, a validator false positive) were *all* only
  caught this way. `rescueflow sim load` + `sim advance` is cheap to run
  repeatedly while developing.
- Run `rescueflow doctor` first in a new environment.
- Commit in small, meaningful checkpoints (2-4 for a prompt-sized unit of
  work), not one giant commit at the end.
- `docs/IMPLEMENTATION_STATUS.md` is the single place that must stay
  accurate after your change -- update it before you report done.

## What's next

Build the reproducible Suite L/T/F evaluation and harden the live demo.
Use independent SUMO processes, matched seeds, demand, incident times
and operator delays. Review [DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)
before changing the workstation. Its Results panel currently reports
observations from individual manual replays. Keep those distinct from
the benchmark CSVs and summary statistics planned for prompt 4.
