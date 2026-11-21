# Integrations (SUMO/TraCI specifics)

The benchmark disables automatic jam teleportation and writes raw SUMO
tripinfo, route edge exit times and warnings. Ordered stop insertion
preserves repeated visits to a road. The service loop checks SUMO's
remaining-stop count and reports missing stops at termination.
Benchmark stops use the approach near the junction, and the response KPI
checks its actual coordinates.

This file is the deep-dive on *how* RescueFlow talks to SUMO -- the
mechanics behind docs/ARCHITECTURE.md's process model. If you are
debugging a TraCI call or changing the execution loop, read this first.

## Process boundary: save-state, not a long-lived server

Every `rescueflow sim ...` CLI command is a **separate OS process**.
TraCI's connection cannot survive a process exiting. Persistence across
commands uses SUMO's own, documented `simulation.saveState(path)` /
`sumo --load-state path` mechanism (`simulation/state/sumo.state.xml.gz`):
a command connects (fresh, or resumed from the saved state), does its one
job, saves state again, and closes. No Python-side state survives between
commands except what gets written into `WorldState`/`world.json` and the
saved SUMO state file.

**A vehicle `traci.vehicle.add`-ed and saved in the same step is silently
dropped on the next process's `--load-state` reconnect.** This was an
actual observed bug, not a theoretical one: `sim load` spawns every
ambulance and immediately saved state without ever calling
`simulationStep()`; the vehicles were simply missing (`getIDList()`
returned `()`) after reconnecting. Fix: always call `self.step(world, n=1)`
once after `_establish_route` and before `save_and_close()`
(`TraciSimulationController.load_scenario` / `apply_committed_plan`).

## Rebuilding Python-side bookkeeping after reconnect

`TraciSimulationController._stop_queue` / `_next_stop_ptr` / `_was_stopped`
are plain Python dicts -- they do not survive a process exit. On every
`connect(resume=True, ...)`, `_rebuild_queues_from_world(world)`
reconstructs them purely from `WorldState`:

- `_stop_queue[vehicle_id]` = that vehicle's `route_stops`, as
  `(stop_id, task_id, stop_kind)` triples (SUMO itself already remembers
  the actual queued `setStop` commands across save/load -- we only need to
  know *which* stop_id corresponds to "whatever the vehicle is currently
  doing" for our own event logging and custody-transfer bookkeeping).
- `_next_stop_ptr[vehicle_id]` = the count of stops whose
  `service_completed_at_s` is already set (contiguous from the start,
  since service happens strictly in route order).
- `_was_stopped[vehicle_id]` = `traci.vehicle.isStopped(vehicle_id)`, read
  fresh right after reconnecting.

This is why `Stop.service_completed_at_s` exists as a field distinct from
`committed` (prompt 1's "locked/in-progress, don't reorder" flag) and from
`departure_s` (the planner's *estimate*, overwritten with the real
completion time once it actually happens).

## Route building: stitching multi-stop itineraries

`TraciSimulationController._build_route` turns an ordered list of `Stop`s
into one real SUMO edge path plus a per-stop `setStop` plan. Two bugs were
found and fixed while building this, both worth knowing about before
touching this code again:

1. **A stop at the vehicle's *own current* node** (no travel needed yet,
   e.g. a pickup at the depot it starts at) cannot be assigned an
   attachment edge until the *next* real segment's departure edge is
   known. Picking an arbitrary edge touching that node produced a real,
   observed bug: a disconnected route
   (`Error: ... has no edges` / `No connection between edge X and edge Y`)
   whenever that arbitrary edge happened to differ from the segment's real
   first edge. Such stops are queued in a local `pending` list and
   resolved once the next real segment's first edge (`departure_edge`) is
   known.
2. **Independently-computed point-to-point segments can disagree at the
   stitch point.** `SumoTravelTimeProvider.road_path_edges(node_a, node_b)`
   finds the best path from *any* edge touching `node_a` to *any* edge
   touching `node_b` -- it does not know which edge a vehicle already
   committed to arriving on. Concatenating two such independently-computed
   segments produced a route where the junction has no actual turn
   connection between the arrival edge and the next segment's chosen
   departure edge (e.g. an immediate U-turn SUMO's generated `<connection>`
   elements don't allow) -- `TraCIException: Vehicle 'R1' has no valid
   route. No connection between edge 'A5A6' and edge 'A6A5'.` Fix: once a
   route already has at least one edge, every subsequent segment is
   computed with `SumoTravelTimeProvider.path_edges_from_edge(from_edge,
   dest_node)`, which continues the Dijkstra from that *specific* edge, so
   every stitch point is graph-connected by construction. Only the very
   first segment (nothing committed yet) uses the node-based
   `road_path_edges`.

Both of these are exercised by `tests/test_sumo_execution.py` and by the
live multi-stop R1 itinerary in the Demo A script (docs/SCENARIOS.md).

## Freezing a broken vehicle exactly

`setSpeed(vehicle_id, 0)` alone left a small (few-meter) residual drift
over many simulation steps in practice -- SUMO's car-following model takes
a step or two to actually decelerate to zero, and (we observed) a further
few meters of drift even after apparent settling. `_enforce_frozen` now
also registers a real `traci.vehicle.setStop(..., duration=1e6)` at the
vehicle's exact current edge/position once its speed has actually reached
~0 (immediately after breaking, `setStop` fails with "too close to brake"
if attempted while still moving, so `setSpeed(0)` is tried every step
first and `setStop` only once `getSpeed() < 0.1`). `setSpeed(0)` remains as
the fallback while mid-junction (`setStop` is not valid on an internal
edge) or before the stop registers. `tests/test_sumo_execution.py`'s
`test_broken_vehicle_never_moves_again` allows the known one-time
deceleration settling distance before asserting true-zero drift
afterward.

## Green-wave signal priority: safety model

See the full design rationale in the `rescueflow.sumo.green_wave` module
docstring; summary:

- Only an *existing* phase index from the junction's own
  `traci.trafficlight.getAllProgramLogics` is ever selected. No arbitrary
  phase/state string is ever written.
- Only a phase that is itself a green phase for *some* movement, and that
  is not the ambulance's target, is ever shortened
  (`traci.trafficlight.setPhaseDuration`), and only down to
  `MIN_GREEN_OVERRIDE_S` (6s). A yellow or all-red clearance phase is
  never touched -- the junction's own safe cyclic sequence is only ridden
  faster, never skipped.
- `traci.vehicle.getNextTLS` gives the correct TraCI link index for the
  vehicle's actual intended movement directly; we never hand-match lane
  IDs to infer it.
- A shortened phase's duration is only ever reduced once per phase
  *instance* (tracked per `(junction, phase index)`), so repeated calls
  across steps don't keep resetting the countdown and freezing the phase.
- Two simultaneous requests at one junction are resolved deterministically
  (priority rank, then ETA); only the winner intervenes, so the question
  "are these two phases compatible" never needs answering -- we simply
  never act on behalf of a second request at the same junction while the
  first is active.
- `REQUEST_TIMEOUT_S` (90s) bounds how long a request can sit un-granted
  (e.g. the vehicle rerouted away) before it is dropped and logged.
- Restoration does not call `traci.trafficlight.setProgram` (which would
  reset the junction to phase index 0, a *worse* disruption than the
  intervention). `setPhaseDuration`'s own documented semantics are "no
  effect on subsequent repetitions of this phase" -- the one-time
  shortening is already self-limiting; "restored" is logged to mark that
  intervention has stopped.

Every request/grant/timeout/restoration is appended to
`GreenWaveController.log` (surfaced by the controller into its
`event_log`, and from there into `simulation/state/sim_event_log.jsonl` by
the CLI).

## Emergency-driving fairness (B0/A1 parity)

Two ambulance SUMO vTypes exist (`rescueflow/sumo/network_gen.py`):
`ambulance` (real `vClass="emergency"` -- red-light passage, rescue-lane
behavior) and `ambulance_routine` (`vClass="passenger"`, no privileges).
`TraciSimulationController._vtype_for_stops` picks `ambulance` iff *any*
stop in the itinerary being established serves a `critical`-priority task,
else `ambulance_routine` -- the same rule regardless of whether B0 or A1
produced the assignment, so a measured green-wave or travel-time benefit
is never an artifact of one policy's vehicles having privileges the
other's don't. Green-wave eligibility (`TraciSimulationController.
_run_green_wave`) independently re-derives "is this vehicle currently on a
critical leg" every step from live task status, so it always matches the
vType rule's intent even though vType itself is fixed for a vehicle's
whole established itinerary (switching exactly mid-route at a leg boundary
would require splitting the route and re-adding there -- not implemented,
not needed: in every demo scenario a given itinerary's priority mix is
simple enough that this does not matter in practice).

## Dashboard integration

The dashboard starts SUMO from an in-process command worker and leaves
that connection open until reset or shutdown. Its controller returns
actual TraCI coordinates, routes, speeds and signal states. A failed
approval can restore that run's SUMO checkpoint. Dashboard sessions
disable SUMO's automatic jam teleportation. When a planned reroute or
breakdown targets an internal junction edge, advance the simulation to a
normal road edge before retrying that action.

The Ljubljana road network is generated with SUMO `netconvert` from the
bundled [OpenStreetMap](https://www.openstreetmap.org/copyright) road
extract, acquired at bbox `14.5075,46.0518,14.5215,46.0595`. The extract
retains highway ways, referenced nodes and usable turn restrictions.
SUMO imports road classes, geometries and OSM signals, merging
intersection signals where its importer infers them. The frontend reads
geometry from that same network. Synthetic service destinations are
anchored to SUMO junction IDs. Background demand is generated from fixed
road flows and uses no live traffic feed.

Hospital drop-offs use an explicit held stop. The controller releases a
vehicle only after its observed service duration and while receiving
capacity remains in the current window. During the hold, the vehicle and
its assets retain custody. The actual service start is stored in the
world so builds using the new stop format can resume mid-hold.

## Native Ljubljana presentation

The [2D presentation](RUNBOOK.md#native-sumo-2d-presentation) builds the
dashboard's road extract into a separate generated directory and uses
the existing TraCI owner with the SUMO GUI binary. One fixed urgent route
and four synthetic civilian flows traverse the Njegoševa and Zaloška
corridor. The blue-light device and vehicle physics are shared by B0 and
A2. Only A2 enables green-wave requests for the ambulance. The displayed
intervention count comes from the controller's phase-shortening events.

The [scene layer](../simulation/presentation/ljubljana.scene.add.xml)
contains [OpenStreetMap](https://www.openstreetmap.org/copyright) building
footprints, parks and other land-use polygons under ODbL 1.0. The source
was the [OSM map extract](https://api.openstreetmap.org/api/0.6/map?bbox=14.5075,46.0518,14.5215,46.0595)
for the same area. SUMO 1.28.0 `polyconvert` used the Ljubljana SUMO
network, [scene typemap](../simulation/presentation/ljubljana.typ.xml),
`--discard --osm.merge-relations 1 --proj.plain-geo` and
`--prune.boundary 632,-15,1285,500`. The included layer retains polygons
with their geographic coordinates. The decoration and top-down vehicle
images are display assets. Routes and signals use SUMO's road network.
