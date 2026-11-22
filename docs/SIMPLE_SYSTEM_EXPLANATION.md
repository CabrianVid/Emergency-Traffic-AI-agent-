# RescueFlow in plain English

Everything here is a synthetic simulation. Nothing connects to real
ambulances, patients or traffic lights.

## Three components

```
 SUMO simulation ──positions, traffic, signals──▶ Central decision system
      ▲                                             │ 1. optimizer: up to 3 feasible options + metrics
      │                                             │ 2. OpenAI Decisions API: ranks those options
      │ approved routes,                            ▼
      └── signal priority ◀── dispatcher approves ── Dashboard
```

1. **Dashboard** shows the live map, fleet, tasks, events and ranked options.
   The dispatcher approves, rejects or recalculates.
2. **Central decision system** turns an unexpected event into feasible plans
   and a recommendation.
3. **SUMO** moves the vehicles, runs the signals and measures what happened.

## Who decides what

- **Optimizer (no AI).** Runs three strategies: urgent first, least
  disruption, nearest free unit. It drops infeasible and duplicate plans, so
  there may be fewer than three. It calculates every ETA and consequence.
- **AI (`gpt-6-luna`).** Answers one multiple-choice question: which of these
  options should be approved? Option probabilities give the ranking. It
  cannot add options, change routes or times, or break a hard constraint.
  The explanation text is generated from calculated metrics, not by the model.
- **Dispatcher.** Makes the final decision. Approval re-validates the plan and
  its version before SUMO executes it. Only the benchmark approves automatically.
- **If the AI fails**, a deterministic rule ranks the options and the screen
  says "Deterministic fallback · not AI". Benchmarks refuse such runs.

Events: new emergency, breakdown, priority change, destination change, road
closure, vehicle delay, equipment request.

## Traffic lights

Technique: **green-phase truncation (early green)** on the junction's existing
program. When an approved urgent unit is 10–25 s away, the conflicting green
may be cut to no less than 6 s. Yellow and all-red always run in full, no
signal colour is ever written directly, one request acts per junction, and
the normal cycle continues afterwards. It was chosen because every phase
shown is one the junction's own safe program already contains.

## Policies

| | Dispatch | Signals |
|---|---|---|
| B0 | Nearest free unit, live-traffic routes | Normal |
| B1 | Central waiting queue, sends a unit as soon as one is free | Normal |
| A1 | Optimizer options, AI picks | Normal |
| A2 | Same as A1 | Priority for critical legs |
| R1 (ablation) | Same options, fixed rule picks, no AI | Normal |

All share the same scenarios, vehicles, blue-light behaviour, traffic data and
25 s decision delay (B1 also re-dispatches at once when a unit frees up).
**Older results use other definitions** (old A2 was B0 +
signals, old A3 was A1 + signals) and are kept under `experiments/published`.

## Results

1,000 scenarios x 5 policies = 5,000 SUMO runs, 4,995 valid.

| | B0 nearest unit | **B1 central queue** | R1 optimizer, no AI | A1 AI dispatch | A2 AI + lights |
|---|---:|---:|---:|---:|---:|
| Valid runs of 1,000 | 999 | 1,000 | 998 | 999 | 999 |
| Mean response, capped at 900 s | 263 s | **187 s** | 226 s | 221 s | 222 s |
| Critical calls, mean capped | 239 s | 181 s | 184 s | 184 s | 184 s |
| P90 of reached calls | 557 s | 331 s | 480 s | 461 s | 473 s |
| Reached within 300 s | 74.1% | **86.0%** | 77.7% | 79.0% | 78.8% |
| Calls not reached | 203/4004 | 45/4007 | 73/3999 | 67/4004 | 68/4004 |
| Critical not reached | 163/2393 | 35/2394 | 31/2389 | 30/2393 | 30/2393 |
| Civilian mean time loss | 78.5 s | 78.6 s | 78.5 s | 78.5 s | 79.0 s |

| Comparison | Seconds saved per scenario | 95% interval | Better / same / worse |
|---|---:|---:|---:|
| B0 -> A1 | 41.1 | 35.0 to 47.5 | 427 / 369 / 203 |
| B0 -> R1 (optimizer) | 35.4 | 29.2 to 41.7 | 417 / 333 / 248 |
| R1 -> A1 (the AI itself) | 5.6 | 2.3 to 8.9 | 206 / 635 / 157 |
| A1 -> A2 (traffic lights) | -1.0 | -2.5 to 0.5 | 202 / 662 / 135 |
| B0 -> B1 | 70.1 | 64.1 to 76.6 | 596 / 331 / 72 |
| **B1 -> A1** | **-29.0** | -34.0 to -24.3 | 216 / 267 / 516 |

What this means:

- **A simple central queue (B1) beats the AI system** by 29 s on average. On
  critical calls they tie (0.4 s, interval -5.3 to 5.7). The difference is in
  less urgent calls (73 s). B1 was added after the first results, because B0
  turned out too weak: it parks waiting calls behind one busy unit.
- **The AI system beats B0**, but most of that comes from the optimizer, and B0
  is not a strong enough opponent to base a claim on.
- **The AI ranking itself adds about 6 s**, only on less urgent calls. It
  disagreed with the fixed rule in 897 of 4,763 A1 rankings.
- **Traffic-light priority shows no measurable benefit** here (12,401 phases
  shortened). Every policy's ambulances already pass red lights on blue light.
- Likely reason for B1's lead, not yet tested: B1 decides again whenever a unit
  becomes free and has no decision delay then, while A1 only decides after an
  event and commits waiting calls to a busy unit.

Audit: 5 runs crashed (seed 139 in B0, R1, A1 and A2; seed 266 in R1). Both bugs
are fixed and the failed runs are kept in the record. Scoring failed runs at the
cap changes no conclusion. No teleports and no ambulance collisions. All AI
rankings are genuine (5,602 new API calls, 6.3M input tokens; the rest reuse
real cached answers) and none is a fallback. 19 A2 runs ended while a signal
request was still open. At some decision
points no feasible plan existed (mostly equipment moved by a breakdown) and
nothing changed: B0 22, B1 50, A1 17, A2 17, R1 14.

## Second generation (2026-11-22, not evaluated on new scenarios yet)

The options offered to the AI and the dispatcher are now urgent first, least
disruption and **waiting queue** (it replaces nearest free unit). A call left
waiting gets an estimated response, so waiting never looks free. When a unit
becomes free for a waiting call, the system decides again; in the dashboard it
pauses and shows options, and the dispatcher still decides.

Benchmark names: R1Q, A1Q and A2Q are R1, A1 and A2 with these changes. The
results above are the older system and still replay exactly.

A 10-scenario check on seeds 0-9 (already seen, so **not evidence**): 50 of 50
runs valid, B0 272 s, B1 169 s, R1Q 163 s, A1Q 163 s, A2Q 171 s. B1 -> A1Q is
+4.0 s with a 95% interval of -0.5 to 12.4: a tie. The AI chose the same option
as the fixed rule in all 34 A1Q rankings.

## Dashboard controls

The AI co-dispatcher is the dark panel on the right. Everything it proposes is
orange: in the call list, units, map and timeline.

| Control | Effect |
|---|---|
| Options, `1`-`4` | Every option shows expected arrivals and the change; one option keeps the plan |
| Approve (`Enter`), Keep plan (`X`), Recalculate (`R`) | Act on the options |
| Traffic-light question | Asked when a priority 1 unit gets a new job: how many lights, yes or no, per unit and job |
| Breakdown, Delay, Road closed | Tell the AI; it asks what to do, with times. A closed road asks about the reroute |
| 30 s review | Unreached priority 1 patients are looked at again every 30 s |
| New emergency (`N`), or right-click the map | Log where it happened (street or map), priority, what, ALS; the AI then suggests |
| Crew report box | Text to a validated event, then "Correct, find a response" |
| Click a unit, call or road | Details and actions; right-click for the same menu |
| Aerial photo, Night | Display |
| Training (`F9`, bottom right) | Run/pause (`Space`), +30 s, restart, speed, area, script, evaluation results |
| `F2` / `F4` / `F8` | Send an emergency call / equipment request / scripted incident |

The clock shows time of day, anchored to the simulated time of the run. The
session starts with an unasked first suggestion and runs on after each decision.

## Run it

Put `OPENAI_API_KEY=...` in `.env` (ignored by git), then:

```powershell
.\.venv\Scripts\python.exe -m uvicorn rescueflow.dashboard.app:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev -- --port 5173
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation baseline
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo run --scenarios 100 --jobs 12
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo report
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo replay --seed 0 --policy A2
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo export
```

`run` resumes where it stopped. `replay` re-executes a recorded run with its
recorded AI answers and checks it reproduces the recorded responses. The same
replay buttons are in the dashboard under **Training > Evaluation results**.

## Where things are

| Feature | Module |
|---|---|
| Options, AI ranking | `decision/alternatives.py`, `decision/agent.py` |
| Planners, safety checks | `planning/` (`queue_dispatch.py` is B1) |
| Events | `events/` |
| Approval | `approval/service.py` |
| SUMO, signals | `sumo/controller.py`, `sumo/green_wave.py` |
| Scenarios, benchmark, replay | `benchmark/scenarios.py`, `benchmark/montecarlo.py` |
| Dashboard | `dashboard/`, `frontend/src/` |

**Kept:** equipment custody and recovery, crew capability, safety validation,
approval versioning, both maps, old benchmark.
**Removed from the active app:** helicopter, hospital capacity limits, fog
scenario, B0/A1 compute buttons.

## Limits

- Planner ETAs are estimates and can be optimistic: one live check predicted a
  worst critical response of 345 s and 529 s was observed.
- No decision-latency sensitivity run was made. The AI's own answer took a
  median 158 ms in the benchmark; one live dashboard call took 823 ms.
- The benchmark is a synthetic 7×7 grid, not Ljubljana and not real demand.
- The city-wide Ljubljana scene is an illustration. It uses the older B0–A3
  policy names and was tuned while looking at its results.
