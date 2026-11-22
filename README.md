# RescueFlow AI

AdaHack 2026 demonstration for Epilog's challenge *"AI-agenti za
obvladovanje nepričakovanih dogodkov pri reševalnih prevozih"*: a
dispatcher decision-support layer that takes an initial transport plan,
recognizes a disruption (vehicle breakdown, new urgent task, ...), proposes
a validated repair, and lets the operator approve it before anything is
committed.

The dispatcher workstation runs synthetic incidents on a real SUMO
network imported from OpenStreetMap around Ljubljana's Tabor and UKC
area. The fictional 7×7 training grid remains available. Traffic demand,
receiving sites and crew messages are simulated.

**Status:** final hackathon build. The local console shows live SUMO telemetry,
validates crew reports, compares constrained schedules, and applies
operator-approved routes. See
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for exactly
what is implemented vs. planned, and
[docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) for the full
source-of-truth design document. **Start here:**
[AGENTS.md](AGENTS.md).

## Start the dispatcher workstation

Install Python 3.11+, Node.js and Eclipse SUMO with its tools. From the
repository root, run the backend in one PowerShell window:

```powershell
py -3.13 -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dashboard,dev]'
.\.venv\Scripts\rescueflow.exe doctor
.\.venv\Scripts\python.exe -m uvicorn rescueflow.dashboard.app:app --host 127.0.0.1 --port 8000
```

In a second window:

```powershell
cd frontend
npm ci
npm run dev -- --port 5173
```

Open [the dispatcher console](http://127.0.0.1:5173/) and follow the
[demo script](docs/DEMO_SCRIPT.md). The bundled Ljubljana road extract
and the training grid both work offline after dependency installation.
The console explicitly labels its rule-based interpretation when an
OpenAI key is absent. See the [runbook](docs/RUNBOOK.md) for optional
OpenAI setup and the API reference.

## Watch the Ljubljana simulation in SUMO 2D

A city-wide scene over a 5 × 4 km aerial photograph of Ljubljana: 10
ambulances, 7 routine transfers, 8 emergency calls and civilian traffic,
with UKC Ljubljana as the receiving hospital. Run each from the
repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation baseline
```

```powershell
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation rescueflow
```

**baseline** sends the nearest free ambulance with normal signals.
**rescueflow** uses the dashboard's decision engine (optimizer plans ranked
by the AI, decided again when a unit frees up) and green-wave priority. Both
use the same frozen network, fleet, calls and traffic. Its box *The difference,
live* compares every call with the recorded baseline run, and squares on the map
mark where it differs. `presentation compare` opens both in step. See the
[runbook](docs/RUNBOOK.md#native-sumo-2d-presentation) for the legend and
reference results.

## Results in one paragraph

On 1,000 matched synthetic scenarios, AI dispatch (A1) responded 41 s faster than
a nearest-unit baseline (B0), but a simple central waiting queue (B1) was another
29 s faster than A1, with a tie on critical calls. The AI ranking itself added
about 6 s and traffic-light priority added nothing measurable. See the
[plain-English guide](docs/SIMPLE_SYSTEM_EXPLANATION.md#results).

```powershell
.\.venv\Scripts\python.exe -m rescueflow.benchmark.montecarlo run --scenarios 10 --policies B0,B1,R1,A1,A2 --jobs 8
```

## Run the older evaluation

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dashboard,benchmark,dev]'
.\.venv\Scripts\python.exe -m rescueflow.benchmark run --suite all --seeds 0:9 --jobs 4
```

The command writes raw SUMO observations, summaries and figures to
`experiments/`. The Results panel separates matched benchmarks from
the current session. For an offline demo, open the
[recorded SUMO replay](experiments/published/replay.html). See
[the experimental methods](docs/EXPERIMENTS.md).

## Quick start (Windows, PowerShell)

```powershell
# One-time setup (offline-friendly: uses the system/conda site-packages)
py -3.13 -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .

# Environment check -- never reports SUMO as working without running it
.\.venv\Scripts\python.exe -m rescueflow.cli.main doctor

# Generate the reproducible SUMO network + deterministic demand
# (--horizon must outlive however long you plan to run a live session for)
.\.venv\Scripts\python.exe -m rescueflow.cli.main sumo setup --horizon 1800

# Real headless SUMO smoke run (genuine traci process, not a fake)
.\.venv\Scripts\python.exe -m rescueflow.cli.main sumo smoke --duration 90

# Build + validate the initial 5-ambulance/7-task scenario
.\.venv\Scripts\python.exe -m rescueflow.cli.main scenario export

# Spawn the fleet into a LIVE SUMO session and let it run
.\.venv\Scripts\python.exe -m rescueflow.cli.main sim load
.\.venv\Scripts\python.exe -m rescueflow.cli.main sim advance --steps 150

# Physically break down R2 at its REAL live position (K1 aboard, no patient)
.\.venv\Scripts\python.exe -m rescueflow.cli.main sim breakdown --vehicle R2

# Compute + approve a repair -- this reroutes the live vehicles too
.\.venv\Scripts\python.exe -m rescueflow.cli.main plan b0
.\.venv\Scripts\python.exe -m rescueflow.cli.main plan approve `
    --proposal simulation/state/proposal_b0.json

# Let it finish: R1 drives to the breakdown, recovers K1, delivers it
.\.venv\Scripts\python.exe -m rescueflow.cli.main sim advance --steps 1700
.\.venv\Scripts\python.exe -m rescueflow.cli.main sim snapshot
```

Full command reference, expected output, and the full Demo A/B/C scripts:
[docs/RUNBOOK.md](docs/RUNBOOK.md) and [docs/SCENARIOS.md](docs/SCENARIOS.md).

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

The suite includes real SUMO execution and dispatcher API integration
tests. See [implementation status](docs/IMPLEMENTATION_STATUS.md) for
the latest verified results.

## Repository layout

```
src/rescueflow/        importable package (the actual system)
  models/               canonical Pydantic contracts (WorldState, Vehicle, Task, ...)
  interfaces/            protocol definitions every module is built against
  scenario/              the fixed demonstration scenario (builder + loader)
  travel/                road/air ETA estimates on the SUMO network graph
  planning/              validator, B0 baseline, A1 optimizer, scoring
  events/                event drafting/validation/application
  dispatch/              mock dispatch notification + navigation handoff stubs
  approval/              the one place a proposal is allowed to commit
  sumo/                  network/route generation + TraCI controller
  cli/                   `rescueflow` command-line entry point
  dashboard/             FastAPI, live SUMO command owner, interpretation
frontend/               React + TypeScript dispatcher workstation
simulation/
  network/                generated SUMO network/routes (gitignored, regenerate with `sumo setup`)
  scenario/               the exported, checked-in initial-plan fixture (stable JSON)
  state/                  mutable working world/proposal JSON (gitignored)
tests/                  pytest suite (see docs/IMPLEMENTATION_STATUS.md for what's covered)
docs/                   architecture, contracts, decisions, integrations, status, runbook, scenarios, traceability
scripts/doctor.py       standalone environment check (no install required)
```
