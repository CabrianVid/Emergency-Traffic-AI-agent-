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
| A1 | Optimizer options, AI picks | Normal |
| A2 | Same as A1 | Priority for critical legs |
| R1 (ablation) | Same options, fixed rule picks, no AI | Normal |

All share the same scenarios, vehicles, blue-light behaviour, traffic data and
25 s decision delay. **Older results use other definitions** (old A2 was B0 +
signals, old A3 was A1 + signals) and are kept under `experiments/published`.

## Dashboard controls

| Control | Effect |
|---|---|
| Play, +30 s, reset, speed | Simulation clock |
| Crew report | Text to a validated breakdown |
| New emergency / Equipment needed | Adds that event |
| Click a unit | Details; report breakdown or 2 min delay |
| Click a task | Details; change priority or hospital |
| Click a road | Close it for 4 minutes |
| Approve, Reject all, Recalculate | Act on the options |
| Authorize traffic-light priority | Green wave for the listed units |

## Run it

Put `OPENAI_API_KEY=...` in `.env` (ignored by git), then:

```powershell
.\.venv\Scripts\python.exe -m uvicorn rescueflow.dashboard.app:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev -- --port 5173
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation baseline
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo run --scenarios 100 --jobs 12
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo report
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo replay --seed 0 --policy A2
```

`run` resumes where it stopped. `replay` re-executes a recorded run with its
recorded AI answers and checks it reproduces the recorded responses.

## Where things are

| Feature | Module |
|---|---|
| Options, AI ranking | `decision/alternatives.py`, `decision/agent.py` |
| Planners, safety checks | `planning/` |
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
- The benchmark is a synthetic 7×7 grid, not Ljubljana and not real demand.
- The city-wide Ljubljana scene is an illustration. It uses the older B0–A3
  policy names and was tuned while looking at its results.
