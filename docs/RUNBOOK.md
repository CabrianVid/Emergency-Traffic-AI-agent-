# Runbook

## Dispatcher startup

Use the two PowerShell commands below from the repository root, in
separate shells. First install the dashboard extra and run
`npm --prefix frontend ci` as shown in the README.

```powershell
.\.venv\Scripts\python.exe -m uvicorn rescueflow.dashboard.app:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev -- --port 5173
```

Open [the console](http://127.0.0.1:5173/). Use one Uvicorn worker per
demo. Stop both servers with Ctrl+C. Use `npm --prefix frontend run build`
to type-check and build the frontend, or `npm --prefix frontend run preview
-- --port 5173` to serve the built UI with the same proxy.

On a fresh checkout the server uses `netconvert` to prepare Ljubljana
from the bundled OpenStreetMap extract, and picks the training grid if
that import or scenario fails. Choose either network in the UI. Set
`RESCUEFLOW_NETWORK=grid` in `.env` to start on the grid.

An optional provider uses `python -m pip install -e '.[dashboard,dev,llm]'`.
Copy `.env.example` to `.env`, add `OPENAI_API_KEY` and optionally set
`OPENAI_MODEL` to a structured-output model, then restart the backend.
The adapter sends the synthetic report and allowed identifiers to the
provider only when a key is configured. Without a key, or on provider
failure, the report panel reports “Rule-based fallback — AI unavailable”
for supported reports. Scenario buttons always use local event drafts.

## Native SUMO 2D presentation

A city-wide Ljubljana scene (about 5 x 4 km: Šiška, Bežigrad, Center,
Tabor, Moste, Trnovo, Vič) with 10 ambulances, 7 routine transfers,
8 emergency calls over 11.5 minutes and about 430 civilian trips. Run one
policy at a time from the repository root:

```powershell
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation baseline
```

```powershell
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation rescueflow
```

`baseline` is B0: the nearest immediately free ambulance and normal
signals. `rescueflow` is A3: fleet-wide urgent-first re-planning (A1)
plus green-wave signal priority. `A1` and `A2` isolate one upgrade each.
Both runs use the same fleet, routine schedule, calls, civilian trips,
blue-light physics and 25 s approval-plus-mobilization delay. Every call
is planned by the real planner and committed through the real approval
service, so the units on screen move because a plan was approved.

How to read the window:

- **Left panel**: policy, legend, simulated clock, calls waiting and
  reached, average and worst response, green-wave count and the latest
  dispatcher event.
- **Red cross**: UKC Ljubljana, the receiving hospital for every patient.
  **Blue house icons**: ambulance stations (Center, Bežigrad, Vič).
- **Pulsing red circle**: an emergency call waiting for an ambulance. It
  turns green when a unit arrives and grey once the patient is at UKC.
- **Ambulance colors**: red = emergency run with blue lights, orange =
  emergency patient on the way to UKC, blue = routine transfer without
  siren, grey = free unit. Small beige cars are civilian traffic.
- **Green dot "GREEN WAVE for Rx"**: a signal phase shortened for that
  unit (RescueFlow and A2 only).

Playback takes about 2–3 minutes at the default `--delay 60` (ms per
simulated second). Use `--delay 20` for faster playback or the GUI's
**Delay** box. The window closes at 30:00 simulated time and the
terminal prints each call's response time.

Reference results on SUMO 1.28.0 (headless, repeated three times for
RescueFlow with identical results):

| | Baseline (B0) | RescueFlow (A3) |
|---|---:|---:|
| Average response (report to first unit on scene) | 7:35 | 3:38 |
| Worst response | 14:37 | 7:29 |
| Calls reached within 5:00 | 2/8 | 6/8 |
| Routine transfers late or unfinished | 0/7 | 4/7 |
| Green-wave phase shortenings | 0 | 6 |
| Civilian mean time loss | 168 s | 199 s |

Baseline keeps routine work untouched and sends whichever unit is free,
so calls that arrive after the three spare units are busy wait 13–14
minutes. RescueFlow diverts units that have not yet picked up a routine
patient, which shortens emergency response but delays routine transfers.
This is one illustrative synthetic scenario, not the matched benchmark.
GUI runs can differ by a few seconds from headless runs.

Use `--output` to keep observations and `--headless` to omit the window:

```powershell
.\.venv\Scripts\python.exe -m rescueflow.sumo.presentation baseline --headless --output simulation/state/presentation/B0
```

Each output directory contains `summary.json`, `events.jsonl` (planner,
approval, stop and signal events), `tripinfo.xml`, `routes.xml` and
`warnings.log`. The matched multi-seed evidence is in
[EXPERIMENTS.md](EXPERIMENTS.md).

## Dispatcher API

FastAPI serves interactive schemas at [API docs](http://127.0.0.1:8000/docs).

| Method and path | Body or output |
| --- | --- |
| GET `/api/state` | One authoritative dashboard snapshot |
| GET `/api/network` | SUMO junction coordinates, road shapes, and signal locations |
| POST `/api/simulation/load` | `{"network":"ljubljana","scenario":"breakdown"}` |
| POST `/api/simulation/reset`, `/play`, `/pause` | Empty request |
| POST `/api/simulation/step` | `{"steps":30}` |
| POST `/api/simulation/speed` | `{"speed":30}` |
| POST `/api/events/interpret` | `{"message":"Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu."}` |
| POST `/api/events` | `{"trigger":"scenario"}`, `{"trigger":"report","report_id":"…"}`, or `{"trigger":"road_closure","edge_id":"…","duration_s":240}` |
| POST `/api/plans/propose` | `{"profiles":["urgent_first","minimum_disruption","baseline"]}` |
| POST `/api/plans/{id}/approve` | `{"expected_plan_revision":4,"idempotency_key":"demo-approval"}` |
| GET `/api/notifications`, `/api/metrics` | Mock dispatch feed and latest measured run results |
| GET `/api/benchmarks` | Generated benchmark report, with a measured bundled fallback |
| WS `/ws` | Full snapshots on change, with HTTP polling fallback in the UI |

The dashboard starts at a reproducible presentation checkpoint, 130s in
Ljubljana and 151s on the grid. Simulated time pauses while the operator
reviews schedules. A new event or observed change to a plan commitment
can make an open proposal stale. Regenerate choices if that happens.

## Benchmarks and offline replay

Run `python -m rescueflow.benchmark run --suite smoke --output experiments/smoke` after installing
the benchmark extra. See [EXPERIMENTS.md](EXPERIMENTS.md) for the full
matrix. The console reads `experiments/benchmark.json` when present;
`RESCUEFLOW_BENCHMARK` can point it to another report. Open
`experiments/published/replay.html` directly to replay recorded SUMO
frames and events without Python, Node or SUMO.

## Stage 2 CLI reference

## Setup (once per machine)

```powershell
cd C:\Users\videk\source\repos\AdaHack
py -3.13 -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m rescueflow.cli.main doctor
```

`--system-site-packages` + `--no-build-isolation --no-deps` is only needed
if your machine has no outbound network access and `pydantic`/`pytest`/
`click`/`setuptools` are already available system-wide (e.g. via Miniconda
base) -- see docs/DECISIONS.md. With normal network access, a plain
`python -m venv .venv && pip install -e .` works too.

If `doctor` reports SUMO missing: install SUMO (https://sumo.dlr.de), then
either set `SUMO_HOME` to the install directory (the folder containing
`bin/` and `tools/`) or ensure `sumo`/`netgenerate` are on `PATH`. Re-run
`doctor` until it reports `ALL CHECKS PASSED`. The Node.js check is
informational only (stage 3 frontend) and does not block anything here.

## Command reference

All commands below assume the venv's Python; prefix with
`.\.venv\Scripts\python.exe -m rescueflow.cli.main` (or just `rescueflow`
if you've activated the venv, since `pyproject.toml` registers that
console script).

| Command | What it does | Idempotent? |
|---|---|---|
| `doctor` | Environment checks with remediation | yes |
| `sumo setup [--seed N] [--amb-start ID] [--amb-dest ID] [--horizon S]` | (Re)generates `simulation/network/{city.net.xml,city.rou.xml,city.sumocfg}` via real `netgenerate` + a deterministic route file | yes (same seed -> same files) |
| `sumo smoke [--duration S]` | Runs a real headless `sumo` process for up to `S` simulated seconds; confirms the ambulance moved and arrived | yes, but requires `sumo setup` first |
| `scenario export [--out PATH]` | Builds the initial scenario, validates feasibility, refuses to write if infeasible, writes `simulation/scenario/scenario_v1.json` and resets `simulation/state/world.json` | yes (deterministic given the same network) |
| `scenario show [--path PATH]` | Prints vehicles/tasks/feasibility for a saved world state | yes, read-only |
| `event breakdown [--state PATH] [--text "..."]` | Parses free text (default: the exact Slovenian R2/K1 report), validates against the world, applies it (mutates + saves state) | **no** -- re-running against an already-broken R2 is rejected (`already_broken`) |
| `plan b0 [--state PATH] [--out PATH]` | Computes the B0 baseline repair proposal, prints it, saves to JSON | yes, read-only w.r.t. state |
| `plan a1 [--state PATH] [--profiles a,b] [--out-dir DIR]` | Computes A1 proposals (one per profile), prints and saves each | yes, read-only w.r.t. state |
| `plan approve --proposal PATH [--state PATH] [--approved-by NAME] [--idempotency-key KEY]` | Re-validates, commits assignments, logs mock dispatch messages, saves state; **if a live `sim` session exists, also reroutes the touched vehicles in it** | yes with the same `--idempotency-key` (defaults to the proposal's own id) |
| `event delay --vehicle ID --seconds S` | `vehicle_delay`: shifts a vehicle's unlocked stops later | no (re-running adds another delay) |
| `event priority --task ID --new-priority P --reason "..."` | `priority_changed` | no |
| `event hospital-capacity --site ID --slots '[{"start_s":...,"end_s":...,"capacity":...}]'` | `hospital_capacity_changed` -- replaces any overlapping existing slot | no |
| `event helicopter-grounded [--vehicle HELI_1] [--duration S] [--cause fog]` | `helicopter_grounded`; auto-restores via a scheduled restoration once the duration elapses (ticked by `sim advance`) | no |
| `sim load [--state PATH]` | Fresh start: spawns every ambulance with stops into a brand-new live SUMO session, saves its state | **no** -- use `sim reset` first to start over |
| `sim reset [--state PATH]` | Deletes the live session's saved state + event log; resets world state to the checked-in fixture | yes |
| `sim advance [--steps N]` | Steps the live simulation `N` simulated seconds; detects dwell completions, transfers custody, runs green-wave, ticks scheduled restorations | yes (always continues from wherever it left off) |
| `sim snapshot [--state PATH]` | Read-only: prints real vehicle positions/task status from the last `load`/`advance` (no SUMO connection) | yes |
| `sim breakdown [--vehicle R2] [--text "..."]` | Validates the breakdown report and checks that any explicit vehicle ID agrees with the report, then freezes that vehicle at its observed live position and applies the event | **no** |
| `sim closure --edge ID [--duration S]` | Live `road_closure`: disallows the edge's lanes in the running session (vehicles already on it keep going) + records it in `WorldState` | **no** -- fails with `already_closed` on a repeat |
| `sim toggle-green-wave --on\|--off` | Enables/disables the green-wave controller for subsequent `sim advance` calls | yes |
| `sim export-run [--out PATH]` | Bundles the current world state + sim event log + solver log into one JSON file | yes |

## The full demo, start to finish

**Pure planning (no live simulation, prompt 1 style):**
```powershell
$cli = ".\.venv\Scripts\python.exe -m rescueflow.cli.main"

& $cli doctor
& $cli sumo setup
& $cli sumo smoke --duration 90
& $cli scenario export
& $cli event breakdown
& $cli plan b0
& $cli plan a1
& $cli plan approve --proposal simulation/state/proposal_a1_minimum_disruption.json
& $cli scenario show
```

**Live SUMO execution (prompt 2 -- see docs/SCENARIOS.md Demo A for the full narrative):**
```powershell
& $cli sumo setup --horizon 1800
& $cli scenario export
& $cli sim load
& $cli sim advance --steps 150
& $cli sim breakdown --vehicle R2
& $cli plan b0
& $cli plan approve --proposal simulation/state/proposal_b0.json
& $cli sim advance --steps 1700
& $cli sim snapshot
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

`tests/test_sumo_smoke.py`, `tests/test_sumo_execution.py` are skipped
(with a stated reason) if SUMO isn't found or
`simulation/network/city.sumocfg` hasn't been generated yet -- run
`sumo setup` first if you want them to actually execute.

## Troubleshooting

- **"Network file ... not found"** from `scenario export` / `plan b0` /
  `plan a1`: run `sumo setup` first (the travel-time provider reads the
  same `.net.xml`).
- **`Error: The route for flow '...' has no edges.` from raw `sumo`**: you
  are looking at a route file written with an `edges=` attribute directly
  on `<vehicle>`/`<flow>` instead of a `<route edges="..."/>` child element
  -- see docs/DECISIONS.md. `network_gen.generate_routes` already does this
  correctly; this note is here in case you hand-edit a route file.
- **`scenario export` refuses with "INFEASIBLE"**: the scenario builder
  itself has a bug (it should never happen with the checked-in builder) --
  check the printed issue codes against docs/CONTRACTS.md's invariant
  table before assuming it's a validator false positive.
- **`event breakdown` says `already_broken`**: you're running it against a
  `world.json` that already has R2 broken. Run `scenario export` again to
  reset `simulation/state/world.json` from the fixture.
- **`plan approve` says `stale proposal`**: the saved proposal's
  `based_on_plan_revision` no longer matches the current state's
  `plan_revision` (e.g. you ran `event breakdown` again, or approved a
  different proposal first, after computing this one). Recompute with
  `plan b0`/`plan a1` against the current state.
- **`Error: The route '!bgN' for vehicle 'bgN.M' is not known.` during
  `sim advance`**: the background flows' `--horizon` expired mid-run. Run
  `sumo setup --horizon 1800` (or longer) before `sim load` for any
  session you'll step past ~150s -- see docs/DECISIONS.md.
- **`TraCIException: Vehicle 'X' has no valid route. No connection
  between edge 'A' and edge 'B'.`**: a real bug class, not user error, if
  you see it after modifying `TraciSimulationController._build_route` --
  see docs/INTEGRATIONS.md "Route building: stitching multi-stop
  itineraries" before changing that code again.
- **A `sim` command prints `()` for `getIDList()` right after `sim load`
  and nothing is spawned**: a vehicle added via TraCI and saved in the
  same step is dropped on reload -- `load_scenario`/`apply_committed_plan`
  must call `self.step(world, n=1)` once after establishing routes and
  before `save_and_close()`. See docs/INTEGRATIONS.md.
- **`sim closure`/`sim breakdown` raises `ModuleNotFoundError: No module
  named 'traci'`**: a stray `import traci` runs before `self.connect(...)`
  has added the SUMO tools directory to `sys.path`. Every such helper must
  call `self.connect(...)` first, then `import traci`.
