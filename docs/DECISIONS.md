# Decisions and known simplifications (prompt 1)

Dated 2026-11-21. Each entry says what was chosen, why, and what the
honest limitation is -- so a later prompt either accepts the tradeoff
knowingly or replaces it deliberately (and updates this file when it does).

## Offline-friendly environment: `--system-site-packages` venv, no pip network access

This sandbox has no outbound network access (`pip install` fails with
`getaddrinfo failed`). `pydantic`, `pytest` and `click` were already
present in the Miniconda **base** environment, so `.venv` was created with
`python -m venv --system-site-packages .venv` against the conda base
interpreter, then `rescueflow` itself was installed with
`pip install --no-build-isolation --no-deps -e .` (no network calls,
`setuptools` was already present locally). If you set this up somewhere
with network access, a plain `python -m venv .venv` + `pip install -e .`
works too; nothing in the code depends on the offline trick.

## SUMO is a system install, not a pip package

`eclipse-sumo` on PyPI was not reachable offline. SUMO 1.28.0 was already
installed at `C:\Program Files (x86)\SUMO` (its own Windows installer).
`rescueflow.sumo.env.resolve_sumo_env()` locates it via `SUMO_HOME`, a
short list of known Windows paths, or `sumo` on `PATH`, and adds
`<home>/tools` to `sys.path` so `sumolib`/`traci` import. This is more
robust than hardcoding one path and still fails loudly (with remediation)
in `rescueflow doctor` if SUMO truly isn't present.

## `<route edges="...">` child element, not an `edges=` attribute

`generate_routes` originally wrote `<vehicle ... edges="A0A1 A1A2 ..."/>`
and `<flow ... edges="..."/>` directly. Plain `sumo` (not `duarouter`)
rejected this with `Error: The route for flow 'bg0' has no edges.` /
`Vehicle 'smoke_amb' has no route.` even though the edge list was correct.
Switching to a `<route edges="..."/>` **child element** of `<vehicle>`/
`<flow>` fixed it immediately. Lesson for later prompts: always smoke-test
a *new* route-file shape against the real `sumo` binary before trusting it.

## Insertion model: pickup+dropoff as a consecutive pair, no splitting

Both B0 and A1 only ever insert a task's pickup and dropoff as two
consecutive new stops into the unlocked tail of a vehicle's route (never
with a third task's own stop forced between them at insertion time). This
is a deliberate simplification of the general pickup-and-delivery VRP,
acceptable for a 5-6 vehicle / 6-8 task hackathon fleet. Note it is *not*
a hard global rule: A1's local-search relocate pass can still end up
interleaving two *different* tasks' pairs (e.g. picking up equipment while
a patient is already aboard) if capacity allows, because it reinserts a
task into whatever the vehicle's current stop list looks like at that
point -- this was observed in practice (`plan a1` output interleaving T1
and T3-RECOVERY on R1) and is physically valid, just not something either
planner deliberately sets out to construct.

## `try_insert` must deep-copy Stop objects per trial (real bug, now fixed)

The first implementation of `try_insert` built each candidate's "trial"
stop list by slicing `vehicle.route_stops` directly
(`list(vehicle.route_stops[start:])`) and then mutating the resulting Stop
objects in place via `recompute_tail`. Because the slice copies the
*list* but not the `Stop` objects inside it, mutating one trial candidate
silently corrupted `vehicle.route_stops` itself and every subsequent trial
in the same `try_insert` call, producing nonsensical zero-cost insertions
and causing every task to pile onto one vehicle regardless of actual
distance. Fixed by `model_copy()`-ing every stop at the top of each loop
iteration. If you add any new code that reads `vehicle.route_stops` and
hands it to something that mutates Stop fields, copy first.

## Breakdown location = vehicle's last confirmed node

`Vehicle.node_id` is not continuously updated with the vehicle's live edge
position in this static, non-executing (prompt 1) model -- there is no
running simulation loop yet (`SimulationController.step`/
`apply_committed_plan` are `NotImplementedError` stubs). So
`events.apply._apply_vehicle_breakdown` uses `vehicle.node_id` (the last
node the vehicle was confirmed at) as the equipment-recovery pickup
location, rather than an interpolated position along the edge it was
travelling. This is an honest, documented approximation, not a bug: stage
2's execution loop should update `Vehicle.node_id` as a byproduct of
real SUMO stepping, after which this location becomes accurate without any
change to the breakdown-handling code itself.

## Road ETA is free-flow shortest path on the static network graph

`SumoTravelTimeProvider.road_eta_s` uses `sumolib`'s shortest path (by edge
length) between a representative outgoing edge of the origin node and
incoming edge of the destination node, summing `length / speed` along that
path. It does not consult live TraCI congestion/signal state. This is the
"interim approximation" the original prompt explicitly allows ("document
an interim approximation that stage 2 will replace" -- see
docs/DEVELOPMENT_PLAN.md section 6.6). It uses the *same* static network
file the SUMO smoke run loads, so it is at least graph-consistent.

## `extra_travel_s` scoring metric is a route-span proxy, not true added distance

`planning/scoring.compute_score`'s `extra_travel_s` is
`max(0, new_route_span - old_route_span)` per touched vehicle (last stop's
departure minus first stop's arrival), summed. It is a reasonable,
documented synthetic proxy for "how much longer this vehicle's day got",
not a precise sum of added kilometers. Treat it as directional, not as a
calibrated cost in currency/fuel terms -- consistent with
docs/DEVELOPMENT_PLAN.md section 6.4's "all weights are synthetic design
choices" framing.

## Deterministic background traffic: hand-picked corner-to-corner flows, not `randomTrips.py`

`sumo.network_gen._BACKGROUND_FLOWS` is four hardcoded (origin, destination,
period) tuples rather than a call to SUMO's `randomTrips.py`. This still
satisfies "identical demand for a given seed" (the tuples never change) and
avoids a dependency on `randomTrips.py`'s own Python environment/CLI
surface during a time-boxed prompt. A later prompt may swap this for
`randomTrips.py` with an explicit `--seed` if genuinely-random, larger-scale
background demand becomes valuable for the benchmark suites in
docs/DEVELOPMENT_PLAN.md section 10 -- note that change here if it happens.

## OR-Tools (stage 2 of the A1 implementation ladder) is not implemented

docs/DEVELOPMENT_PLAN.md section 6.3 describes OR-Tools as "recommended,
conditional" on top of a working stage-1 deterministic insertion/local
search. Stage 1 (`planning/insertion.py`, `planning/optimized.py`) is
implemented and tested; OR-Tools is not attempted in prompt 1 to keep scope
inside the exit gate. See docs/IMPLEMENTATION_STATUS.md for what this means
for prompt 2 planning.
