# Implementation status

The [AdaHack product film](../video/README.md) uses this branch's operator desk,
recorded SUMO positions and published first-generation benchmark. Its editable
100-second Remotion composition renders independently of the application.

## Current: simplified decision flow (2026-11-22)

Branch `simplify-decision-flow`. Plain-English guide with all results:
[SIMPLE_SYSTEM_EXPLANATION.md](SIMPLE_SYSTEM_EXPLANATION.md).

- **Working and verified in SUMO:** seven dashboard events, up to three ranked
  options, approve/reject/recalculate, signal priority, live OpenAI ranking with
  a labeled fallback, Monte Carlo benchmark, replay (headless and in the window,
  from the dashboard and from a folder holding only the published bundle).
- **Dashboard city:** live area is 5.2 x 4 km of Ljubljana (2,589 road edges, 140 signals);
  the dispatcher can log an emergency at any reachable place (798 junctions).
- **Dashboard:** redesigned as an operator desk (2026-11-22): AI co-dispatcher panel,
  simulation controls behind Training (`F9`), aerial base map. Checked in the browser
  at 1600x900 and 1366x768.
- **Tests:** 76 pass (`pytest tests/ -q`), frontend builds. No automated test calls
  the real API; live ranking was checked by hand and by the benchmark.
- **Measured:** 5,000 runs, 4,995 valid. The B1 central-queue baseline beats AI
  dispatch by 29.0 s [24.3, 34.0]; AI dispatch beats B0 by 41.1 s; the AI ranking
  adds 5.6 s; signal priority adds nothing.
- **Second generation built, not evaluated:** waiting-queue option, critical calls to
  the unit that arrives first (30 seen scenarios: R1Q 169.8 s, B1 169.2 s, a tie), free-unit
  trigger (benchmark and dashboard), schedules re-estimated from now, and engine 2
  (below). Checked on 10 seen scenarios only: 50/50 runs valid.
- **Next step:** freeze, then run the fresh seeds (about 2.5-3.5 h, about $1):
  `python -m rescueflow.benchmark.montecarlo run --root experiments/mc-queue --first-seed 1000 --scenarios 1000 --policies B0,B1,R1Q,A1Q,A2Q --jobs 12`
- **Limitations:** a unit that cannot change lane at the scene is taken off the road
  until its lane has a gap (can exceed a minute); cars can still gridlock a junction;
  a breakdown inside a junction must wait a step; no latency sensitivity run; the
  city-wide scene is an illustration with older policy names.

Everything below describes earlier stages. It mentions removed features
(helicopter, fog, hospital capacity) and older policy names.

# Earlier: Implementation status — final AdaHack handoff

Updated 2026-11-21. Verified on Windows 11, Python 3.13.9, Node 22,
and Eclipse SUMO 1.28.0. Benchmark implementation checkpoint
`54c3f7f` and dashboard/audit checkpoint `abec998` preceded the
evidence and handoff commit. Use `git log -3 --oneline` to see the
final repository tip. The published report records each experiment's
code SHA and SUMO version.

## Readiness

The local demo is ready using the no-key rule-based interpreter.
The browser walkthrough reset the Ljubljana scenario, interpreted the
Slovenian R2/K1 report, displayed two alternatives, approved one,
showed the mock crew messages, and observed K1 reaching H1 in SUMO.
It also exercised speed, play/pause, reset, the urgent and fog scenario
controls, grid/Ljubljana switching, map zoom, and the published
Results panel. The UI reset now keeps one live map and dispatcher.

The benchmark and offline replay work without model credentials.
Setup, sample report paths and the four-minute walkthrough are in the
[README](../README.md), [runbook](RUNBOOK.md) and [demo script](DEMO_SCRIPT.md).

## Native SUMO 2D presentation (city-wide)

Updated 2026-11-22. `rescueflow.sumo.presentation` now plays a 5 × 4 km
Ljubljana scene built from a wider OSM road extract
(`simulation/presentation/ljubljana-wide-roads.osm.gz`) over a matching
2024 GURS aerial (`ljubljana-wide-aerial.jpg`). Ten ambulances, seven
routine transfers, eight emergency calls and ~430 civilian trips run
through the benchmark's `LogisticsRun`, so each call is planned by the
real B0 or A1 planner and committed through `DefaultApprovalService`.
A left-hand panel, hospital and station icons, pulsing call markers and
state-colored units are drawn by `CityView` through new controller GUI
helpers (`gui_marker`, `gui_vehicle_view`, `gui_frame`).

```powershell
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation baseline
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation rescueflow
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Both policies complete headless on SUMO 1.28.0 with no teleports. One run
each (reached calls / average over reached calls): B0 8/8, 7:50; A1 8/8,
4:47; A2 7/8, 7:48; A3 7/8, 4:51. A3 did not reach CALL-8. Earlier
published figures (A3 3:38, B0 7:35) are withdrawn: `SUMO_PROCESSING` was
left empty, so SUMO teleported R2 in B0 and R2 and R10 in A3. See the
runbook for the table and the caveat on how the scene was tuned.

Building the larger scene exposed and fixed real issues:

- **A1 `urgent_first` could put routine work ahead of a critical call** on
  the same vehicle (a routine insertion was cheapest in front of it).
  `try_insert(..., keep_priority_order=True)` now forbids inserting a task
  ahead of a pending pickup of a more urgent task for that profile. See
  DECISIONS.md. **The published A1/A3 benchmark numbers predate this fix**
  (B0/A2 reproduce exactly; a seed-0 urgent smoke run gave A1 CALL-2
  249 s instead of the published 1998 s). Rerun the matrix before
  quoting A1/A3 figures.
- **Re-inserting a moving unit** failed in several situations. The
  controller now caps the re-insertion speed (`reroute_speed_capped`),
  clamps stops ahead, replaces queued insertions, and retries a blocked
  insertion once (`reinsertion_speed_relaxed`). This is a mitigation, not
  a full fix: A1 and A3 runs still log SUMO "will not be able to depart"
  errors for R8 (unpriorised junction too close).
- `_enforce_frozen` read every vehicle's position twice per step and
  discarded it (about 235 s of a 272 s profiled run). Removing it made
  runs much faster; the demand also changed, so no clean speedup factor
  was measured.
- Opt-in `defer_junction_reroutes` (presentation only) commits a plan while
  a unit is inside a junction and installs its route on the next road edge.

Known limitations: the scene uses free-flow ETAs, so a unit stuck in a
queue can still look close to the planner. SUMO 1.28.0 crashed natively
in un-joined variants of this network; the shipped joined, actuated
network did not crash in any run.

## Recorded measurements

The assembled matrix contains 168 real SUMO runs. The audit accepted
160 and excluded eight. Seeds 0–9 were attempted in five conditions.
Closure and urgent cases also ran seed 10. The closure exclusions
were B0 and A1 at seed 1 with recorded collision observations. The
urgent exclusions were all policies at seed 9 and A1/A3 at seed 10.
Their stop-skip warnings could not be confirmed as a same-time
route replacement. Earlier exploratory and superseded outputs remain
locally under `experiments/smoke*/`.

### Urgent response, synthetic training grid

| Policy | Valid seeds | Calls reached | Mean (s) | Median (s) | P90 (s) | Within 300s |
|---|---:|---:|---:|---:|---:|---:|
| B0 | 10 | 20/20 | 1381.15 | 1299 | 2029 | 0% |
| A1 | 9 | 18/18 | 978.06 | 640 | 2020 | 50% |
| A2 | 10 | 20/20 | 1379.40 | 1292 | 2019 | 0% |
| A3 | 9 | 18/18 | 974.72 | 632 | 2017 | 50% |

Matched call-level mean savings were 382.78s for B0→A1 (28.13%),
1.75s for B0→A2 (0.13%), 3.33s for A1→A3 (0.34%) and 386.11s for
B0→A3 (28.37%). Those four comparisons used 9, 10, 9 and 9 valid
matched seeds. No confidence intervals were emitted at these counts.

### Logistics and traffic

Correct K1 recovery was observed in 6/10 B0 and A1 runs and 4/10 A2
and A3 runs. The smaller served-task tardiness with A2/A3 coincided
with more high-priority tasks unfinished. The replay shows the
prerequisite recovery and dependent transfer from A3, seed 0.

The fixed-route experiment used ten valid pairs in each of two traffic
conditions. Priority saved 37.0s eastbound and 53.3s northbound on
average. Civilian mean time loss decreased in both conditions, while
mean run P90 increased by 2.09s and 0.29s respectively. These are
synthetic grid outcomes. The plots, target denominators and rejected
run details are linked from [EXPERIMENTS.md](EXPERIMENTS.md).

## Verification and commands

```powershell
.\.venv\Scripts\rescueflow.exe doctor
.\.venv\Scripts\python.exe -m pytest tests/ -v
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite smoke --output experiments/smoke --jobs 8
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite smoke --seeds 0 --output experiments/smoke-3 --jobs 6
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite all --seeds 0:9 --jobs 8
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite logistics --scenario urgent --seeds 0:9 --jobs 5 --output experiments/smoke-audited-urgent
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite logistics --scenario urgent --seeds 10 --jobs 4 --output experiments/smoke-extra-urgent
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite logistics --scenario closure --seeds 10 --jobs 4 --output experiments/smoke-extra-closure
.\.venv\Scripts\python.exe -m rescueflow.benchmark report
```

Doctor, all 51 tests and the production frontend build passed. The
first 48-run smoke identified invalid stop ordering and a closure
fixture conflict. A corrected one-seed smoke passed 16/16.
The main matrix recorded 123/160 under the stricter original warning
rule. The urgent audit rerun recorded 36/40, urgency seed 10 recorded
2/4, and closure seed 10 recorded 4/4. The final report uses the
audited urgency outputs and both seed-10 conditions. A clean Git
archive independently reproduced the eastbound seed-0 B0/A2 probe
travel times and intervention counts.

Use [known limitations](KNOWN_LIMITATIONS.md) when presenting.
Suite F, OR-Tools, idle repositioning and the optional live provider
verification remain outside the measured handoff.
