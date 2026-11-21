# Implementation status -- prompt 2/4

**Date:** 2026-11-21
**Git commit:** `9a84a06` ("Add green-wave signal priority, emergency-
driving fairness, and prompt-2 docs"), on top of `d02a834` (disruption
events + solver hardening) and `27a6199` (live SUMO execution loop), after
the prompt 1 commits `9c5296d`/`e3b42cb`/`73c0a5a`/`5d5b68c`. Run
`git log --oneline` to confirm this is still the tip before trusting the
rest of this file.
**Environment:** same as prompt 1 (Windows 11, Python 3.13.9 via
`--system-site-packages` venv, Eclipse SUMO 1.28.0, no outbound network
access in this session).

## What prompt 2 actually delivers (and how it was verified)

Everything below was verified by **actually running it against the real
SUMO simulator**, not by reading the code. Several real bugs were found
and fixed this way -- see docs/DECISIONS.md "Prompt 2 decisions" and
docs/INTEGRATIONS.md for the specifics; this section only states the
final, working behavior.

1. **Live execution loop** (`TraciSimulationController`): `sim load`
   spawns every ambulance that has stops into a real SUMO session;
   `sim advance` steps it, detecting real dwell begin/completion via
   `isStopped()` transitions, transferring asset custody and completing
   tasks *only* on real completion, and mirroring live
   edge/position into `WorldState`. Verified over a continuous ~1850s
   live run: all 7 scenario tasks (T1-T7) completed via genuine SUMO
   dwell detection, with `Stop.service_completed_at_s` persisted correctly
   across separate CLI process invocations (via SUMO's own
   save-state/load-state, not a kept-alive process).
2. **Physical breakdown + equipment recovery**: `sim breakdown --vehicle
   R2` physically freezes R2 at its *real* live edge/position (observed:
   froze at edge `A1A2`, node `A2` -- not the static approximation
   prompt 1 used), strands K1 there, and the broken vehicle's position
   never changes again afterward (verified over 50+ further steps, within
   a documented few-meter one-time deceleration-settling tolerance -- see
   docs/INTEGRATIONS.md). `plan b0`/`plan approve` then physically
   rerouted R1 to drive to the real breakdown node, dwell 180s (the
   transfer interval), and deliver K1 to the hospital -- K1's tracked
   location moved `vehicle:R2` -> `roadside:A2` -> `vehicle:R1` ->
   `hospital:A6`, never teleported, never duplicated, confirmed by
   `tests/test_sumo_execution.py` and a full manual live run.
3. **Road closures**: `sim closure --edge ID [--duration S]` disallows the
   edge's lanes in the live session (vehicles already on it are not
   ejected) and records the closure in `WorldState.closed_edges`;
   `SumoTravelTimeProvider` (a hand-rolled Dijkstra, since
   `sumolib.Net.getShortestPath` has no edge-exclusion parameter) routes
   every subsequent B0/A1 query around it identically, with zero
   planner-side code change. Auto-reopens via `ScheduledRestoration` if
   `--duration` was given.
4. **Four new executed event types**: `vehicle_delay`, `priority_changed`,
   `hospital_capacity_changed`, `helicopter_grounded` are now fully typed,
   validated, *and* applied (prompt 1 only typed them).
   `helicopter_grounded` auto-restores via the same generic
   `ScheduledRestoration` mechanism as road-closure reopening, ticked by
   every `sim advance` (and independently testable without SUMO via
   `events.apply.tick_scheduled_restorations`).
5. **A1 hard wall-clock solve budget + solver audit log**: both
   `BaselinePlanner`/`OptimizedPlanner` accept a `solve_budget_s` and stop
   (reporting the rest unserved, logged) if exceeded; every candidate
   insertion attempt, rejection, and final selected plan's score/ETA
   assumption is appended to `simulation/state/solver_log.jsonl`
   (`rescueflow.planning.solver_log.SolverLog`).
6. **Green-wave signal priority**: a full, safety-constrained
   implementation (`rescueflow.sumo.green_wave.GreenWaveController`) --
   see docs/INTEGRATIONS.md for the exact safety model. Verified in a live
   run: real requests, real `setPhaseDuration` shortenings (never a
   skipped yellow/all-red), real grants, timeouts, and restorations, for
   multiple junctions along a critical-priority vehicle's route in
   sequence. Pure-logic conflict-resolution (priority rank, then ETA) is
   unit-tested without SUMO in `tests/test_green_wave.py`.
7. **Emergency-driving fairness**: two ambulance SUMO vTypes
   (`ambulance` = real `vClass=emergency`; `ambulance_routine` =
   `vClass=passenger`), assigned by the *same* rule
   (`TraciSimulationController._vtype_for_stops`) regardless of whether
   B0 or A1 produced the assignment -- verified live: R3 (critical
   patient) got `ambulance`/`emergency`, R2 and R5 (non-critical) got
   `ambulance_routine`/`passenger`.
8. **Dispatch adapter, approval gate**: unchanged from prompt 1 (still
   the only path allowed to commit), extended so `plan approve`
   automatically pushes the approved reroute into a live `sim` session if
   one exists (`TraciSimulationController.apply_committed_plan`).
9. **Repositioning (item 9 in the prompt)**: **not implemented**, as
   explicitly permitted ("leave this documented stretch feature disabled
   rather than half-implementing it"). No code, no stub, no claim.

## Stable programmatic controls (CLI)

`sim load` / `reset` / `advance` / `breakdown` / `closure` /
`toggle-green-wave` / `export-run`, plus the four new `event` subcommands
(`delay`/`priority`/`hospital-capacity`/`helicopter-grounded`). See
docs/RUNBOOK.md for the full reference and the exact Demo A/B/C scripts in
docs/SCENARIOS.md. These are plain CLI commands today (as prompt 2 allows:
"CLI or Python service"); each wraps exactly one `TraciSimulationController`
/ planner / event-application call with no logic of its own, so a future
FastAPI wrapper (prompt 3) can call the same underlying functions directly
instead of shelling out to the CLI.

## Test results

```
.\.venv\Scripts\python.exe -m pytest tests/ -v
...
42 passed in ~11s
```

27 tests carried over unchanged from prompt 1 (per the prompt's "reuse the
stage 1 tests, do not overwrite them when they expose a bug" -- none
needed to be weakened; two *production* bugs they didn't cover were found
instead via live runs and fixed, see docs/DECISIONS.md). 15 new:
`tests/test_sumo_execution.py` (3, live SUMO: pickup/dropoff + custody
transfer, broken-vehicle freeze, live closure), `tests/test_green_wave.py`
(4, pure logic, no SUMO), `tests/test_events_prompt2.py` (8, the four new
event types' validation/application/auto-restoration).

## Known limitations / explicitly out of scope for prompt 2

- OR-Tools (A1 stage 2) still not implemented -- optional per the plan,
  deferred again (docs/DECISIONS.md).
- Repositioning not implemented (explicitly permitted to skip rather than
  half-build).
- `vehicle_restored` (manual recovery of a broken vehicle) is typed but
  not executed -- not in prompt 2's required list either.
- A single spawned vehicle's SUMO type (`ambulance` vs `ambulance_routine`)
  is fixed for its whole established itinerary, not switched exactly at a
  leg boundary mid-route -- documented in docs/INTEGRATIONS.md; green-wave
  eligibility itself is unaffected (recomputed every step from live task
  status).
- No FastAPI/frontend wrapper -- explicitly prompt 3 scope; the CLI
  commands are already structured 1:1 against plain functions so that
  wrapper should be straightforward.
- Benchmark suites (Suite L/T/F, docs/DEVELOPMENT_PLAN.md section 10) --
  `BenchmarkRunner` remains a protocol only.

## Recommended next prompt (prompt 3 scope)

1. FastAPI wrapper around the existing `rescueflow.*` functions (not the
   CLI) -- `scenario`, `events`, `planning`, `approval`,
   `sumo.controller` already have no CLI-specific logic baked in.
2. LLM-backed `EventInterpreter` (stage 3 of docs/DEVELOPMENT_PLAN.md
   section 9) behind the same `EventInterpreter` protocol, falling back to
   the existing rule-based parser/structured commands exactly as
   docs/CONTRACTS.md 9.1 requires (absent API key must never block).
3. Dispatcher dashboard frontend (map + before/after timeline + plan
   cards + mock dispatch notification feed) -- consume the `sim`/`plan`
   JSON outputs directly; `simulation/state/sim_event_log.jsonl` and
   `solver_log.jsonl` are already structured for this.
4. Only after 1-3: the Suite L/T/F benchmark runner and OR-Tools stage 2,
   per docs/DEVELOPMENT_PLAN.md's own sequencing.
