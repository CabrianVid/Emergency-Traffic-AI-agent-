# Synthetic SUMO experiments

Start with the [published charts and summary data](../experiments/published/matched-grid-ljubljana/).
The multi-seed comparisons below are separate from the single city-wide scene in the [runbook](RUNBOOK.md#native-sumo-2d-presentation).

## Final matched grid and Ljubljana evaluation

Use headless SUMO and a fresh output root. Compare `B0` and `B1` with
`R1Q` (rule-ranked queue-aware options), `A1Q` (AI ranking on the same
options), and `A2Q` (AI ranking and green-wave priority). The AI
policies require a working Decisions API connection. An AI fallback
does not become evidence for either AI policy.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dashboard,llm,benchmark]'
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo run --root experiments/mc-final-local/grid --first-seed 1000 --scenarios 110 --policies B0,B1,R1Q,A1Q,A2Q --jobs 12
.\.venv\Scripts\python.exe -m rescueflow.benchmark.ljubljana --root experiments/mc-final-local/ljubljana --first-seed 1000 --scenarios 26 --jobs 4
.\.venv\Scripts\python.exe -m rescueflow.benchmark.final_report experiments/mc-final-local
```

The two launch commands can run in separate shells. Tune the sum of
worker counts for available CPU and API throughput. Ljubljana uses the
bundled OpenStreetMap road graph and the existing joined, actuated SUMO
signal and junction settings. Call locations and demand are synthetic.
The generator freezes six ambulances, two modeled receiving locations,
two to six calls, disruptions and three traffic levels per seed. The
city and grid seeds use different scenario generators. Pair policies
within each map. Both maps keep the same modeled 25-second dispatch
delay and emergency driving rules across policies. The city settings
disable junction collision checks. This limits safety conclusions
about those intersections. The final city run used seeds 1000–1025,
including six consecutive reserve seeds to account for invalid runs.

The timing charts display B1, A1Q and A2Q. The report gives a missed
call a 900-second score and averages each
scenario equally. Percent improvement divides paired seconds saved by
the paired reference mean. It also reports five-minute reach, P90 among
reached calls, civilian delay, phase interventions and wins, ties and
losses. The primary comparisons retain every valid matched pair and
record invalid runs separately. The summary, Seaborn figures and CSVs
are in the output root's `report/` folder.

To reproduce the focused signal-priority tests, generate fresh traffic
inputs and reuse them for matched A1Q and A2Q dispatch. Each case fixes
one qualified unit and one critical call on a training-grid corridor.
The two policies share blue-light privileges and dispatch delay.

```powershell
$run = 'experiments/mc-final-local'
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite traffic --seeds 1200:1211 --jobs 8 --output "$run/priority-stress"
.\.venv\Scripts\python.exe -m rescueflow.benchmark.priority_stress "$run/priority-stress" "$run/priority-dispatch" --jobs 12
.\.venv\Scripts\python.exe -m rescueflow.benchmark.final_report $run
```

The focused result appears separately in the last chart. Monthly impact
uses the measured difference on calls reached by both policies. It scales
the observed change by a hypothetical number of comparable calls per
month. Survival would require local outcomes and a validated estimate of
the effect of response-time changes on specific conditions.

## Monte Carlo benchmark (2026-11-22)

1,000 seeded scenarios on the 7x7 grid: six ambulances, 2-6 emergencies,
ongoing transports, and a random mix of breakdowns, delays, priority and
destination changes, closures and equipment requests. World, routes, network
and events are frozen and hashed; every policy replays the same inputs with a
25 s approval and mobilization delay and the same blue-light physics.

| Policy | Dispatch | Signals |
|---|---|---|
| B0 | Nearest free unit, live-traffic routing | Normal |
| B1 | Central priority queue, re-dispatch when a unit frees up, divert unstarted routine work | Normal |
| R1 | Same options as A1, deterministic rule picks, no AI | Normal |
| A1 | Up to three feasible options, OpenAI Decisions API picks | Normal |
| A2 | Same as A1 | Green-wave priority for critical legs |

- **Metric, fixed before any run:** response from call to eligible arrival,
  capped at 900 s; a call nobody reaches scores 900 s. Scenario mean, paired by
  scenario, 95% bootstrap interval. Sensitivity: failed runs scored at the cap.
- **B1 was added afterwards.** The audit of the first 4,000 runs showed B0 never
  moves a waiting call to a unit that frees up. B0's results are unchanged.
  B1 alone gets an extra dispatch trigger, with no decision delay there.
- **AI runs may not use the fallback.** A failed AI call leaves no result and the
  run is repeated. Cached answers are real earlier answers to identical input.
- Seeds 0-99 were also the pilot. Between pilot and main run only two crash
  fixes were made.

```powershell
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo run --scenarios 1000 --policies B0,B1,R1,A1,A2 --jobs 12
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo report
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo export
```

Results: [plain-English guide](SIMPLE_SYSTEM_EXPLANATION.md#results). Data:
`experiments/published/montecarlo-2026-11-22` (`summary.json`, `runs.csv`,
`replay-data.zip`). The AI policies need `OPENAI_API_KEY`.

## Older matched benchmark

Policy names below are older: its A2 is B0 plus signals and its A3 is A1 plus
signals. Its A1/A3 numbers predate a planner fix.

Evaluation uses the 7×7 training grid. The live workstation defaults
to the OpenStreetMap-derived Ljubljana road map. Demand, calls, delays
and receiving sites are synthetic in both settings.

## Reproduce

From the repository root in PowerShell, with SUMO 1.28 available:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dashboard,benchmark,dev]'
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite smoke --seeds 0:2 --output experiments/smoke --jobs 4
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite all --seeds 0:9 --jobs 8
.\.venv\Scripts\python.exe -m rescueflow.benchmark report
```

Extend the standard matrix with the same seed-10 conditions used in
the published evidence:

```powershell
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite logistics --scenario urgent --seeds 10 --jobs 4
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite logistics --scenario closure --seeds 10 --jobs 4
```

Use `--suite logistics` or `--suite traffic` to run a single suite.
`--scenario urgent` selects the emergency response condition.
`--policies B0,A1` works for logistics; traffic always isolates B0/A2.
Exit code 2 means at least one run failed an evaluation gate. The report
is still emitted. Use a separate `--output` for a different matrix.

## Matched policies and inputs

| Policy | Dispatch and repair | Signals |
|---|---|---|
| B0 | Nearest immediately free eligible ambulance for new critical work. If all are committed, select the earliest feasible pickup after existing work. Greedy local insertion for other affected tasks. | Normal |
| A1 | Fleet-wide urgent-first reinsertion on the same SUMO graph with locked stops preserved | Normal |
| A2 | B0 dispatch and repair | Coordinated priority |
| A3 | A1 dispatch and repair | Coordinated priority |

Each condition and seed has a fixed initial schedule, backlog, fleet,
and explicit per-vehicle departures, speed factors and edge lists.
A separate RNG stream controls urgent location order. A manifest hashes
the network, demand, configuration and initial state. Each policy
verifies those hashes and starts an independent Python worker and SUMO
process. All policies use the same vTypes and real SUMO blue-light
devices for emergency vehicles. Logistics runs use 15s of modeled
approval and 10s of modeled mobilization. Any additional junction
clearance is recorded. Solver runtime is measured separately in wall
milliseconds. Typed inputs keep the benchmark's LLM runtime field null.

## Suite L

1. **Breakdown.** R2 carries K1 and no patient. It breaks down at
   151s. Recovery requires a different unit and a 180s transfer.
   The dependent patient transfer is released only after K1 reaches
   H1 custody.
2. **Urgent calls.** Two ALS requests arrive at 151s and 181s while
   scheduled work continues. R4 also has ALS in the benchmark fixture.
3. **Closure.** A4A5 closes at 151s for 600s. It lies on a committed
   ambulance route. The frozen civilian corridors bypass A4A5 throughout.

The cutoff is 2,800 simulation seconds. Weighted tardiness sums
observed lateness of completed deadline-bearing tasks with synthetic
weights 10/4/1 for critical/high/routine. Read it alongside unserved
counts. The on-time denominator includes unserved tasks that had a
deadline. The interrupted original equipment task is replaced by its
recovery task in the tally. An incomplete recovery can leave its
dependent task unreleased and unserved.

Emergency response runs from report to first observed pickup by an
eligible ambulance, within 15m of the declared call node. Mean,
median and nearest-rank P90 describe observed arrivals. The reach
target is 300s and its denominator includes every emergency. Deltas
compare the same scenario, seed and jointly reached call IDs in valid
pairs. Positive seconds indicate savings. Relative savings require a
positive reference. The report bootstraps paired seed differences
only at 20 or more pairs with responses.

## Suite T

B0 and A2 use the same frozen urgent mission, blue-light vehicle and
civilian trips. The two conditions are low-demand eastbound and
peak-demand northbound. Emergency travel and waiting are observed in
tripinfo. Civilian mean and P90 time loss use arrived civilian trips,
then give equal weight to seeds. Unfinished includes trips that never
entered and trips still in motion at cutoff. Logs include requests,
restoration, phases shortened, seconds removed and simulator warnings.
Conflict ranking is unit-tested. Both traffic conditions use a single
emergency mission per run.

## Audit and outputs

- `experiments/inputs/<suite>/<condition>/<seed>/` contains the frozen
  manifest, network, world and demand.
- `experiments/runs/<suite>/<condition>/<seed>/<policy>/` contains
  `metrics.json`, `events.jsonl`, `tasks.csv`, `vehicles.csv`,
  `tripinfo.xml`, `routes.xml`, `warnings.log` and `process.log`.
  Metrics record the benchmark code SHA and SUMO version.
- `experiments/summary.csv`, `paired_differences.csv` and
  `effects.csv` expose each condition and contrast. Figures are saved
  under `experiments/charts/`. Invalid pairs remain in the paired CSV,
  but are excluded from effects.
- `experiments/benchmark.json` drives the dashboard Results panel
  when present. `experiments/published/` contains the small measured
  output bundle for a fresh checkout and a standalone recorded replay.

Published figures include [response time](../experiments/published/charts/response.png),
[logistics tardiness](../experiments/published/charts/logistics-tardiness.png),
[unserved logistics tasks](../experiments/published/charts/logistics-unserved.png),
[fixed-route ambulance travel](../experiments/published/charts/traffic-ambulance.png),
and [civilian time loss](../experiments/published/charts/traffic-civilian.png).

Hard constraint violations, skipped required stops, missing observed
service, leaked priority requests, simulator failures, collisions,
teleports and unreadable trip files are invalid. Artifacts are kept
for audit. Unserved tasks and trips still in progress are also kept.
SUMO can warn that a stop was skipped while an old route is being
explicitly replaced. The audit records those warnings separately when
tripinfo confirms both the removal and restart of that exact vehicle
at the warning time. A real missing stop still fails its gate.
