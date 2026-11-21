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
| `plan approve --proposal PATH [--state PATH] [--approved-by NAME] [--idempotency-key KEY]` | Re-validates, commits assignments, logs mock dispatch messages, saves state | yes with the same `--idempotency-key` (defaults to the proposal's own id) |

## The full demo, start to finish

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

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

`tests/test_sumo_smoke.py` is skipped (with a stated reason) if SUMO isn't
found or `simulation/network/city.sumocfg` hasn't been generated yet --
run `sumo setup` first if you want it to actually execute.

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
