# Requirements traceability (prompt 1)

Maps each explicit requirement in the prompt 1 brief to where it is
actually implemented and how it was verified. "Verified" means a command
was actually run and its output observed, not inferred.

| # | Requirement | Implementation | Verified by |
|---|---|---|---|
| Contract 1 | Python 3.11+, Pydantic, `src/` layout, local venv, locked-enough deps | `pyproject.toml`, `src/rescueflow/`, `.venv` (`--system-site-packages`, offline) | `doctor` PASS on Python 3.13.9; `pip list` pinned ranges in `pyproject.toml` |
| Contract 2 | Canonical typed `WorldState` w/ `plan_revision`/`telemetry_seq`, `Vehicle`, `Task`, `Stop`, `Asset`, `ReceivingSite`, `Event`, `PlanProposal`, `PlanEvaluation`, `DispatchMessage`, `OperatorDecision`, `SimulationSnapshot` | `src/rescueflow/models/core.py` | `tests/test_contracts.py` (6 tests) |
| Contract 3 | Protocol interfaces for every named module | `src/rescueflow/interfaces/protocols.py` (all 10 named: `ScenarioLoader`, `TravelTimeProvider`, `Planner`, `PlanValidator`, `ApprovalService`, `DispatchNotificationAdapter`, `NavigationHandoff`, `SimulationController`, `EventInterpreter`, `BenchmarkRunner`) | `runtime_checkable`; concrete classes satisfy each (see docs/ARCHITECTURE.md) |
| Contract 4 | Pure candidate planning; revision/TTL/idempotency-gated approval | `planning/baseline.py`, `planning/optimized.py` (never mutate `world`); `approval/service.py` | `tests/test_approval.py` (4 tests) |
| Contract 5 | Operator/safety invariants | `planning/validator.py` | `tests/test_validator_invariants.py` (5 tests) + `tests/test_events.py::test_occupied_ambulance_breakdown_escalates_instead_of_inventing_handoff` |
| A | Environment inspection + `doctor` command | `cli/doctor.py`, `scripts/doctor.py` | `rescueflow doctor` -> ALL CHECKS PASSED (see docs/IMPLEMENTATION_STATUS.md log) |
| B | Repo/docs scaffold | this `docs/` tree, `README.md`, `AGENTS.md`, `.gitignore`, `.env.example` | present in this commit |
| C | Reproducible SUMO seed, genuine smoke run | `sumo/network_gen.py` (`netgenerate` + deterministic `<route>` flows), `sumo/controller.py::run_smoke` | `rescueflow sumo smoke` -> `RESULT: OK`, real SUMO 1.28.0 process, `tests/test_sumo_smoke.py` |
| D | Initial synthetic plan: 5 ambulances + 1 helicopter, 2 hospitals, 6-10 tasks, multiple trips/time windows, K1 aboard R2, >=2 vehicles busy | `scenario/loader.py::build_initial_world` | docs/SCENARIOS.md; `rescueflow scenario export` validates feasibility before writing |
| E | B0 conventional baseline | `planning/baseline.py` | `tests/test_planners.py` (ALS exclusivity, unaffected-assignment preservation) |
| F | A1 enhanced planner, >=2 honest profiles | `planning/optimized.py` (+ shared `planning/insertion.py`) | `tests/test_planners.py::test_a1_can_relocate_unlocked_assigned_task`; `plan a1` CLI output shows two genuinely different outcomes, not fabricated |
| G | Typed `vehicle_breakdown`/`new_urgent_task` (+ typed-only models for the rest) | `events/types.py`, `events/interpreter.py`, `events/apply.py`; `EventType` enum has all 8 types, only 2 executed | `tests/test_events.py`; `apply_event` raises `NotImplementedError` naming the type for the other 6 |
| H | Documented CLI commands; same-network ETA provider | `cli/main.py`; `travel/sumo_travel_time.py` reads the same `.net.xml` the smoke test loads | docs/RUNBOOK.md; every command below was actually run (docs/IMPLEMENTATION_STATUS.md) |

## Prompt 2 requirements

| # | Requirement | Implementation | Verified by |
|---|---|---|---|
| 1 | Single-owner `SimulationController`, headless, pause/resume/reset, seed, snapshots | `sumo/controller.py` (save-state based resume, `step`/`reset`), `sim advance` prints `SimulationSnapshot` | Live multi-invocation run (docs/SCENARIOS.md Demo A); no silent fallback -- `connect()` raises if SUMO is missing |
| 2 | Physical service execution: dwell, custody only on completion, broken vehicle never sent onward | `_process_arrivals_and_stops`/`_complete_stop`/`_enforce_frozen` | `tests/test_sumo_execution.py` (3 tests); live Demo A run (K1 custody trace) |
| 3 | Road disruptions: planned closure, same for B0/A1, no teleport | `events/apply._apply_road_closure`, `travel/sumo_travel_time.py` closure-aware Dijkstra, `sumo/controller.apply_road_closure_physically` | `tests/test_sumo_execution.py::test_live_road_closure_blocks_routing`, `tests/test_events_prompt2.py` |
| 4 | `vehicle_delay`/`priority_changed`/`hospital_capacity_changed`/`helicopter_grounded`, analytical helicopter ETA (unchanged, never in SUMO), infeasibility report preserved | `events/apply.py` (4 new handlers + `tick_scheduled_restorations`), `events/interpreter.py` (4 new validators) | `tests/test_events_prompt2.py` (8 tests) |
| 5 | A1 wall-clock solve budget, >=2 strategies, solver log; B0 parity | `planning/optimized.py`/`baseline.py` (`solve_budget_s`), `planning/solver_log.py` | existing planner tests still pass; manual CLI run shows `solver_log.jsonl` populated |
| 6 | Green wave: real phases, min-green/yellow/all-red respected, 1 then 3 junctions, timeout/restore, conflict resolution | `sumo/green_wave.py` | `tests/test_green_wave.py` (pure logic); live Demo A run (real `phase_shortened`/`priority_green_reached`/`restored` sequence across multiple junctions) |
| 7 | Emergency-driving fairness, same model for all policies | `sumo/network_gen.py` (two vTypes), `sumo/controller._vtype_for_stops` | Live check: R3 (critical) = `emergency`, R2/R5 (non-critical) = `passenger` |
| 8 | Dispatch adapter on committed changes only, operator gate + idempotency preserved | `approval/service.py` (unchanged) + `plan approve` now also calls `apply_committed_plan` for a live session | prompt 1 approval tests still pass unmodified |
| 9 | Repositioning | **not implemented**, per the prompt's own permission to skip rather than half-build | docs/IMPLEMENTATION_STATUS.md |

## Exit gate -- prompt 1 (from that prompt)

> A clean, documented CLI run loads an initially valid schedule, processes
> the R2+K1 disruption, obtains at least one valid A1 repair and one B0
> repair if feasible, approves a choice, records a mock crew assignment
> message, and independently executes a real SUMO vehicle smoke run.

All satisfied in one session, in this order, with real output (see
docs/IMPLEMENTATION_STATUS.md for the full transcript):
`scenario export` (feasible) -> `event breakdown` (R2+K1) -> `plan b0`
(feasible, 0 unserved) -> `plan a1` (both profiles feasible, 0 unserved)
-> `plan approve` (committed, 6 mock dispatch messages logged) ->
`sumo smoke` (real SUMO 1.28.0 process, ambulance moved and arrived) --
run independently of the planning pipeline, confirming SUMO itself works
regardless of the JSON-level planning demo.

## Exit gate -- prompt 2 (from that prompt)

> Genuine SUMO simulation demonstrates approved plan changing physical
> vehicle routing, time-accurate equipment pickup and delivery,
> constraints respected, and green-wave changing/restoring at least one
> signal safely.

All satisfied in one live, continuous-state session (docs/SCENARIOS.md
Demo A is the exact script): `sim load` -> `sim advance` (R2 genuinely
en route carrying K1) -> `sim breakdown --vehicle R2` (physically frozen
at its real position, never moves again) -> `plan b0` + `plan approve`
(R1's route is **physically changed** in the live SUMO session, not just
in `WorldState`) -> `sim advance` to completion: R1 genuinely drives to
the real breakdown position, dwells the transfer interval, and delivers
K1 -- every hard constraint (capacity, custody, no double-pickup, no
teleport) held throughout, enforced by the same `DefaultPlanValidator`
prompt 1 built -- and multiple real green-wave requests along R1's and
R3's routes were granted (`setPhaseDuration` shortenings that never
skipped a yellow/all-red phase) and explicitly restored once each
passage was confirmed or timed out.
