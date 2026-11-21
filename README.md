# RescueFlow AI

AdaHack 2026 demonstration for Epilog's challenge *"AI-agenti za
obvladovanje nepričakovanih dogodkov pri reševalnih prevozih"*: a
dispatcher decision-support layer that takes an initial transport plan,
recognizes a disruption (vehicle breakdown, new urgent task, ...), proposes
a validated repair, and lets the operator approve it before anything is
committed.

This is a synthetic demonstration on a **fictional 7x7 SUMO road grid**. It
is not a real dispatch system, not connected to any real emergency service,
and uses no real patient data, no Google Maps API key, and no live traffic
feed.

**Status:** prompt 1 of 4 (bootstrap, contracts, baseline/optimizer
planners, one verified SUMO slice). See
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for exactly
what is implemented vs. planned, and
[docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) for the full
source-of-truth design document. **Start here:**
[AGENTS.md](AGENTS.md).

## Quick start (Windows, PowerShell)

```powershell
# One-time setup (offline-friendly: uses the system/conda site-packages)
py -3.13 -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .

# Environment check -- never reports SUMO as working without running it
.\.venv\Scripts\python.exe -m rescueflow.cli.main doctor

# Generate the reproducible SUMO network + deterministic demand
.\.venv\Scripts\python.exe -m rescueflow.cli.main sumo setup

# Real headless SUMO smoke run (genuine traci process, not a fake)
.\.venv\Scripts\python.exe -m rescueflow.cli.main sumo smoke --duration 90

# Build + validate the initial 5-ambulance/1-helicopter/7-task scenario
.\.venv\Scripts\python.exe -m rescueflow.cli.main scenario export

# Apply the R2/K1 breakdown report (Slovenian free text, rule-based parser)
.\.venv\Scripts\python.exe -m rescueflow.cli.main event breakdown

# Compute the two repair policies
.\.venv\Scripts\python.exe -m rescueflow.cli.main plan b0
.\.venv\Scripts\python.exe -m rescueflow.cli.main plan a1

# Approve one (revalidates, commits, logs mock dispatch notifications)
.\.venv\Scripts\python.exe -m rescueflow.cli.main plan approve `
    --proposal simulation/state/proposal_a1_minimum_disruption.json

# Inspect the committed world state
.\.venv\Scripts\python.exe -m rescueflow.cli.main scenario show
```

Full command reference, expected output and troubleshooting:
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

27 tests, all passing as of this prompt (see
docs/IMPLEMENTATION_STATUS.md for the exact run log and commit hash),
including one real (not skipped on this machine) SUMO smoke test.

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
simulation/
  network/                generated SUMO network/routes (gitignored, regenerate with `sumo setup`)
  scenario/               the exported, checked-in initial-plan fixture (stable JSON)
  state/                  mutable working world/proposal JSON (gitignored)
tests/                  pytest suite (see docs/IMPLEMENTATION_STATUS.md for what's covered)
docs/                   architecture, contracts, decisions, status, runbook, scenarios, traceability
scripts/doctor.py       standalone environment check (no install required)
```
