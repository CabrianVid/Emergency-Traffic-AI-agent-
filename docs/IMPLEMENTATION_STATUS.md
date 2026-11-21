# Implementation status — final AdaHack handoff

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
Live OpenAI parsing was untested because no key was configured.
Setup, sample report paths and the four-minute walkthrough are in the
[README](../README.md), [runbook](RUNBOOK.md) and [demo script](DEMO_SCRIPT.md).

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
