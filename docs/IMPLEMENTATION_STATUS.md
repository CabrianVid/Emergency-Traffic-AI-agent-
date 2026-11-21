# Implementation status -- prompt 1/4

**Date:** 2026-11-21
**Git commit:** see `git log -1` at handoff time (this file is updated in
the same commit it describes; check the commit this file was last modified
in).
**Environment this was built/verified on:** Windows 11, Python 3.13.9
(Miniconda base via `--system-site-packages` venv, no outbound network
access during this session), Eclipse SUMO 1.28.0 at
`C:\Program Files (x86)\SUMO`, git 2.x (mingw64), Node v22.20.0 (unused by
prompt 1).

## What is actually implemented (and verified by running it)

- **Contracts**: all canonical types from the brief
  (`WorldState`/`Vehicle`/`Task`/`Stop`/`Asset`/`ReceivingSite`/`Event`/
  `PlanProposal`/`PlanEvaluation`/`DispatchMessage`/`OperatorDecision`/
  `SimulationSnapshot`) in `src/rescueflow/models/core.py`, schema-versioned
  (`SCHEMA_VERSION = "1.0.0"`), all ten named protocol interfaces in
  `src/rescueflow/interfaces/protocols.py`.
- **`doctor`**: real pass/fail checks (Python, pydantic/click/pytest, git,
  node, SUMO binaries, sumolib/traci import) with remediation text.
  Verified: `rescueflow doctor` -> `ALL CHECKS PASSED` on this machine.
- **Reproducible SUMO network + genuine smoke run**: `netgenerate`-built
  7x7 grid (400m spacing, traffic lights), a deterministic route file (4
  fixed background flows + one `vClass=emergency` ambulance, all built
  from real `sumolib` shortest paths on the generated network, not
  hand-drawn), and a real headless `sumo`/TraCI smoke run. Verified:
  `rescueflow sumo setup` then `rescueflow sumo smoke --duration 90` ->
  ```
  SUMO version: Eclipse SUMO sumo 1.28.0
  Wall time: 643.0 ms; simulated time elapsed: 60.0 s
  Detail: ambulance visited 3 distinct edges, arrived=True, total arrivals (all vehicles)=1
  RESULT: OK
  ```
- **Initial scenario**: 5 ambulances + 1 helicopter, 2 hospitals (one with
  a capacity-constrained time window), 7 tasks, K1 aboard R2, R3 carrying a
  locked critical patient, R5 pre-assigned but not started. All stop ETAs
  computed from the real SUMO network graph. Verified feasible via
  `rescueflow scenario export` (refuses to write if infeasible). Full
  description: docs/SCENARIOS.md.
- **B0 baseline**: greedy nearest-eligible-feasible repair,
  `src/rescueflow/planning/baseline.py`. Verified: produces a feasible,
  fully-served repair for the R2/K1 breakdown (`plan b0` -> `feasible=True
  unserved=[]`), and a dedicated test proves it never assigns an
  ALS-requiring task to a closer BLS-only vehicle.
- **A1 optimizer (stage 1 of the implementation ladder)**: fleet-wide
  reinsertion over pending *and* not-yet-started-but-assigned tasks, plus
  a bounded swap/relocate local search for the `minimum_disruption`
  profile, `src/rescueflow/planning/optimized.py` +
  `src/rescueflow/planning/insertion.py`. Verified: both `urgent_first` and
  `minimum_disruption` profiles are feasible with 0 unserved tasks on the
  breakdown scenario, and they are genuinely different plans with
  genuinely different scores (`minimum_disruption` achieved
  `critical_tardiness_s=0.0` vs. `urgent_first`'s `1733.1`, by also
  relocating T5 off R5 -- an emergent result of the search, not scripted).
  **OR-Tools (stage 2) is not implemented** -- see docs/DECISIONS.md.
- **Events**: `vehicle_breakdown` and `new_urgent_task` are fully typed,
  validated, and executed (`events/apply.py`); the Slovenian example
  ("Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.") is parsed by a
  rule-based regex interpreter and produces the expected
  `vehicle_breakdown` draft. `road_closure`, `priority_changed`,
  `hospital_capacity_changed`, `helicopter_grounded`, `vehicle_delay`,
  `vehicle_restored` have typed `EventType` values and would validate-fail
  with a clear `not_implemented` `ValidationError`, or
  `apply_event` raises `NotImplementedError` naming the type -- these are
  **data models only**, not executed, per the prompt's explicit allowance.
- **Occupied-ambulance safety escalation**: if a *patient* (not just
  equipment) is aboard a vehicle that breaks down, the task is marked
  `requires_operator_intervention` and no recovery task is invented --
  verified by a dedicated test (the demo scenario itself never exercises
  this path, since R2 carries only equipment, per the brief's own
  simplification).
- **Mock dispatch notifications**: created only on committed (approved)
  assignments, never for a candidate proposal. Verified: `plan approve`
  logged 6 `[MOCK DISPATCH]` lines, one per newly/re-assigned task.
- **Approval gate**: revision-match, TTL, fresh re-validation, and true
  idempotency (same key -> cached result, no double-apply) -- all four
  have a dedicated passing test.
- **CLI**: `doctor`, `sumo setup`/`smoke`, `scenario export`/`show`,
  `event breakdown`, `plan b0`/`a1`/`approve` -- every one of these was
  actually run this session (see docs/RUNBOOK.md for the full transcript
  and troubleshooting notes).

## Test results

```
.\.venv\Scripts\python.exe -m pytest tests/ -v
...
27 passed in 1.55s
```

Including `tests/test_sumo_smoke.py::test_smoke_ambulance_moves_and_arrives`
(actually executed, not skipped, on this machine). See
docs/REQUIREMENTS_TRACEABILITY.md for which test covers which required
invariant.

## Known limitations / explicitly out of scope for prompt 1

- No continuous SUMO execution loop: `SimulationController.load_scenario`,
  `.step` beyond the smoke test, and `.apply_committed_plan` are either
  minimal or `NotImplementedError` stubs. The committed plan's stop times
  are *estimates*, not SUMO-replayed. This is the main prompt 2 item.
- No frontend (correctly out of scope -- "Frontend is stage 3; no frontend
  required yet").
- No LLM-backed `EventInterpreter`; the rule-based regex parser only
  handles the one documented free-text pattern (vehicle breakdown). Any
  other free text raises `EventParseError` ("AI interpretation
  unavailable") rather than guessing, per the brief.
- Road ETAs are static-network free-flow shortest paths, not live-TraCI
  congestion-aware -- documented interim approximation (docs/DECISIONS.md).
- `road_closure`, `priority_changed`, `hospital_capacity_changed`,
  `helicopter_grounded`, `vehicle_delay`, `vehicle_restored` are typed but
  not executed.
- No green-wave signal priority subsystem (section 8 of
  docs/DEVELOPMENT_PLAN.md) -- not in prompt 1's scope.
- No benchmark runner / Suite L/T/F metrics harness (section 10) --
  `BenchmarkRunner` is a protocol only, no implementation yet.
- `extra_travel_s` is a route-span proxy, not exact added distance (see
  docs/DECISIONS.md).

## Recommended next prompt (prompt 2 scope)

1. Implement `TraciSimulationController.load_scenario`/
   `apply_committed_plan`/a real stepping loop that maps committed `Stop`s
   onto TraCI vehicle routes/stops, updates `Vehicle.node_id` and task
   status as stops are actually reached (this also makes the
   breakdown-location approximation in docs/DECISIONS.md obsolete).
2. Execute `road_closure` and `hospital_capacity_changed` (their validator
   support already exists); `helicopter_grounded` next (validator support
   already exists too); `priority_changed` last.
3. Refresh `SumoTravelTimeProvider` road ETAs from live TraCI state
   (`simulation.findRoute` / edge occupancy) instead of the static
   free-flow graph, once there is a running simulation to query.
4. Only after 1-3 work end-to-end: OR-Tools stage-2 optimizer, green-wave
   signal priority (section 8), and the Suite L/T benchmark runner
   (section 10) -- in that order, per docs/DEVELOPMENT_PLAN.md's own
   sequencing.

Do not start the stage-3 frontend or LLM agent orchestration before 1-3
above produce a genuinely executing (not just planned) simulation loop --
see docs/DEVELOPMENT_PLAN.md's overall sequencing and AGENTS.md.
