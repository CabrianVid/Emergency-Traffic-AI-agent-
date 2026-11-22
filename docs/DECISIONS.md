# Decisions and known simplifications

## Final experiment decisions

- Use the training grid for the evaluation and Ljubljana for the
  presentation. The benchmark's emergency type equips the same
  blue-light device in all policy runs.
- Preserve scheduled stop order with TraCI's indexed insertion. A
  smoke run showed that setting a stop by edge can move a repeat visit
  to an earlier occurrence of that edge. Treat a missing stop as
  missing, and count history for load without applying current
  schedule timing comparisons to old observations.
- Freeze civilian trips on open corridors in the closure condition.
  Early smoke runs confirmed that SUMO will terminate when a
  pre-scheduled civilian route enters an edge closed to that class.
- Keep unreached emergencies in the 300s target and availability
  counts. Compare response time among calls jointly reached, so
  a policy cannot gain seconds merely by dropping hard-to-reach calls.

## City-wide presentation decisions (2026-11-22)

- **A1 `urgent_first` keeps priority order per vehicle.** The profile's
  documented intent is to serve critical work sooner, but greedy
  insertion could place a later routine task in front of an already
  inserted critical pickup on the same unit, delaying a cardiac arrest
  by minutes. Insertions may no longer precede a pending pickup of a
  more urgent task in that profile. B0 and `minimum_disruption` are
  unchanged. All existing tests pass; published A1/A3 benchmark numbers
  predate this and must be regenerated before reuse.
- **Junction-deferred reroutes are opt-in.** The presentation and, since
  the decision-flow rework, the dashboard commit an approved plan
  immediately and install a mid-junction unit's route on its next road
  edge. The benchmark keeps the stricter behavior.
- **Scene uses actuated, joined signals.** Guessed fixed-time programs
  gave ~4 min civilian delay on every corridor; actuated control keeps
  traffic moving while leaving the green wave something to do.

Dated 2026-11-21. Each entry says what was chosen, why, and what the
honest limitation is -- so a later prompt either accepts the tradeoff
knowingly or replaces it deliberately (and updates this file when it does).
Prompt 1 entries are below; prompt 2 entries (SUMO execution, green wave,
emergency fairness) are at the bottom. See also docs/INTEGRATIONS.md for
the deeper TraCI-level mechanics behind several of these.

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

## Breakdown location = vehicle's last confirmed node (prompt 1; resolved in prompt 2)

Prompt 1 had no running simulation loop, so `Vehicle.node_id` was never
updated from a live position, and `events.apply._apply_vehicle_breakdown`
used the last *planned* node as an approximation. **Resolved in prompt 2**
exactly as predicted here: `TraciSimulationController.
freeze_vehicle_and_locate` now reads the vehicle's real live edge via
TraCI, converts it to a node (`edge.getToNode()`, or the junction id
itself for an internal edge), and writes it into `Vehicle.node_id` *before*
`events.apply.apply_event` runs -- so the existing breakdown-handling code
needed zero changes and now uses a real position. See
`docs/SCENARIOS.md` Demo A for the observed result (R2 frozen and K1
stranded at its actual live edge, not a guess).

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
implemented and tested; OR-Tools is still not attempted (prompt 1 or 2) to
keep scope inside each prompt's exit gate -- a possible prompt 3/4 item if
the small-fleet heuristic ever stops being "good enough".

---

# Prompt 2 decisions

## Process model: SUMO save-state/load-state, not a long-lived server

Each `rescueflow sim ...` CLI command is a separate process; persistence
across commands uses SUMO's own `simulation.saveState`/`--load-state`
mechanism, not a kept-alive Python process or a reimplemented serializer.
Full rationale and the real bugs this surfaced (a vehicle added and saved
in the same step was silently dropped on reload; stitched multi-stop
routes could reference a junction turn SUMO doesn't actually connect) are
in docs/INTEGRATIONS.md -- read that before changing
`TraciSimulationController`.

## A real, order-dependent circular import between `sumo` and `travel`

`rescueflow/sumo/__init__.py` used to eagerly re-export
`TraciSimulationController`, which imports `rescueflow.travel.
sumo_travel_time`, which imports `rescueflow.sumo.env` (part of the `sumo`
package). Depending on which package got imported *first* in a given
process, this either worked (by accident, via Python's tolerance for
partial-module submodule imports) or failed with "cannot import name
... from partially initialized module". Fixed by not re-exporting
`TraciSimulationController` from `rescueflow/sumo/__init__.py` at all --
import it directly: `from rescueflow.sumo.controller import
TraciSimulationController`. If you add a new cross-package re-export to
any `__init__.py`, check both import orders actually work, not just the
one your editor happens to exercise first.

## Validator: a completed stop is history, not a re-litigable claim

`DefaultPlanValidator._check_asset_custody` and `._check_time_windows` now
skip any `Stop` with `service_completed_at_s is not None`. Without this, a
historical pickup stop (e.g. R2's completed K1 pickup, left in
`route_stops` as a permanent record) was re-checked against the *current*
(since-moved-on) asset location every time any new proposal was validated
-- a real false-positive ("asset K1 is physically at roadside:A2, not at
pickup node B1") that made every subsequent repair proposal for an
unrelated vehicle look infeasible. `_check_capacity_and_ordering` and
`_check_eligibility` are deliberately *not* given this skip: the former
needs completed stops to maintain correct running onboard/capacity state
for what comes after them, and the latter checks static facts (vehicle
capabilities, task mode) that don't change over time, so it can't produce
this class of false positive.

## Background-flow horizon must outlive the demo, not just the smoke test

`sumo setup`'s default `--horizon` (route-file/`sumocfg` `<end>` and
background `<flow>` `end=`) was 150s, sized for the prompt-1 smoke test.
Stepping a live session well past that produced `Error: The route '!bg2'
for vehicle 'bg2.3' is not known.` (a flow trying to keep repeating after
its own declared demand window expired, interacting badly with
save-state's auto-generated per-repetition route names). Fixed by passing
a generous `--horizon` (1800s, covers a full multi-event demo) when
setting up for a live `sim` session; the 150s default remains fine for the
standalone `sumo smoke` command, which only runs ~60-90s.

## Freeze-settling tolerance in `test_broken_vehicle_never_moves_again`

See docs/INTEGRATIONS.md "Freezing a broken vehicle exactly" -- real
deceleration takes a step or two and a few meters. The test allows that
one-time settling (`controller.step(world, n=5)`) before asserting
true-zero drift over many further steps, rather than asserting an
instant, physically-impossible stop.

## Emergency-driving fairness: two vTypes, one shared rule

See docs/INTEGRATIONS.md "Emergency-driving fairness" for the full
mechanism. The short version: `ambulance` (real `vClass=emergency`) is
used only for an itinerary containing a critical-priority leg;
`ambulance_routine` (`vClass=passenger`) otherwise; both B0- and
A1-produced assignments are typed by the exact same rule
(`TraciSimulationController._vtype_for_stops`), so neither policy's
measured performance can be an artifact of unequal SUMO driving
privileges.
# Prompt 3 decisions

## Receiving slots govern live handover completion

All hospital drop-offs use a held SUMO stop. After the observed dwell
has elapsed, the controller resumes only as many stopped vehicles as
the receiving window permits. The world records the actual service
start so the dwell survives CLI save-state and resume. Custody remains
aboard until departure from the stop. Validation counts completed
handovers in their actual completion windows and future reservations
by their estimated arrival windows.

## Compare A1 alternatives by their schedules

Alternatives with the same assigned task IDs can differ in vehicle,
order, location or timing. Compare complete schedules and unserved
tasks, ignoring generated proposal and stop IDs.
