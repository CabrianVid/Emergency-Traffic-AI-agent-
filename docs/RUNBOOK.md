# Runbook

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
| `sim breakdown [--vehicle R2] [--text "..."]` | Live `vehicle_breakdown`: physically freezes the vehicle at its **real** live position first, then applies the normal validated event using that position | **no** |
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
