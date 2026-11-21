# Scenarios

## The fixed initial scenario (`scenario.loader.build_initial_world`)

Built once, validated for feasibility, and exported to the checked-in
fixture `simulation/scenario/scenario_v1.json` (regenerate with
`rescueflow scenario export`; it must stay feasible or export refuses to
write the file). All node IDs are real junction IDs in the generated 7x7
SUMO grid (`simulation/network/city.net.xml`, 400m spacing, so they exist
on the actual simulated road network, not invented coordinates).

Fleet (5 ambulances + 1 abstract helicopter, matching the "at least five
road ambulances and one abstract helicopter" requirement):

| Vehicle | Mode | Capabilities | Node | Status at scenario start |
|---|---|---|---|---|
| R1 | ambulance | ALS | A0 | available |
| R2 | ambulance | BLS | B1 | **carrying** K1, en route to deliver it (T3) -- this is the vehicle that breaks down |
| R3 | ambulance | BLS | D3 | **carrying** a critical patient (T2), locked in-progress leg |
| R4 | ambulance | BLS | F5 | available |
| R5 | ambulance | BLS | G5 | assigned to T5 (not yet started -- relocatable by A1, not by B0) |
| HELI_1 | helicopter | ALS | D6 | available (unused by any task in this scenario; demonstrates the analytical 200km/h model is wired up, see `travel/sumo_travel_time.py`) |

Two receiving sites: H1 (node A6) with a deliberately capacity-constrained
window -- 3 slots for 0-500s, **0 slots for 500-900s**, 3 slots again after
900s (this mirrors the exact example in docs/DEVELOPMENT_PLAN.md section
6.5) -- and H2 (node G0) with unconstrained capacity.

Seven tasks (within the "6-10" requirement):

| Task | Kind | Priority | Pickup -> Dropoff | Status |
|---|---|---|---|---|
| T1 | patient_transport | high | A1 -> A6 (H1) | pending |
| T2 | patient_transport | critical | D2 -> G0 (H2) | **en_route_dropoff on R3, hard deadline, locked** |
| T3 | equipment_delivery | high | B1 -> A6 (H1) | **en_route_dropoff on R2, carrying K1** |
| T4 | equipment_delivery | routine | F5 -> G0 (H2) | pending, requires asset K2 (at depot F5) |
| T5 | patient_transport | routine | G5 -> G0 (H2) | planned on R5, not yet started |
| T6 | urgent_response | critical | C4 -> A6 (H1) | pending, requires ALS (only R1 qualifies) |
| T7 | patient_transport | high | E2 -> G0 (H2) | pending |

Two tracked assets: K1 (aboard R2, `in_transit`) and K2 (at depot F5,
`available`).

`world.sim_time_s = 120.0`: the snapshot moment the dispatcher receives the
breakdown report, chosen so R2 and R3 are genuinely mid-leg (departed their
pickup, not yet arrived at dropoff) rather than at an arbitrary t=0.

This gives: multiple vehicle trips, time windows (soft and one hard),
capacity, priorities, an ALS-capability constraint, a capacity-constrained
hospital window, one tracked equipment asset mid-transit, and at least two
vehicles already busy with locked legs -- matching
docs/DEVELOPMENT_PLAN.md section 3.1's "at least three vehicles have
commitments" demo requirement (R2, R3, R5 all have pre-existing
assignments).

## Demo A: the central R2/K1 breakdown story -- now a genuine live SUMO run

Prompt 1's version was JSON-level planning only. Prompt 2 replays it as a
real, physically-executing simulation:

```powershell
rescueflow sumo setup --horizon 1800   # background flows must outlive the whole demo
rescueflow scenario export
rescueflow sim load                     # spawns every ambulance with stops into a live SUMO session
rescueflow sim advance --steps 150      # R2 is now genuinely en route, carrying K1
rescueflow sim breakdown --vehicle R2   # physically freezes R2 at its REAL live position
rescueflow plan b0                      # or plan a1
rescueflow plan approve --proposal simulation/state/proposal_b0.json   # auto-reroutes live vehicles
rescueflow sim advance --steps 1700     # let the repaired plan actually run to completion
rescueflow sim snapshot
```

Observed result in this repo (see docs/IMPLEMENTATION_STATUS.md for the
full run log): R2 is frozen at its genuine live edge/position (e.g.
`A1A2`, node `A2` -- not a static guess), K1's location moves to
`roadside:A2` -- the *actual* breakdown spot --, T3 is interrupted and
superseded by `T3-RECOVERY`. The approved repair reroutes R1 live; R1
physically drives to A2, dwells for the 180s transfer, and drives on to
deliver K1 to H1 (A6) -- `K1`'s final tracked location is
`hospital:A6`, `available`, having only ever been `vehicle:R2` ->
`roadside:A2` -> `vehicle:R1` -> `hospital:A6`: never teleported, never
duplicated. Every other task (T1, T2, T4, T5, T6, T7) also completes via
real dwell detection, and green-wave priority requests/grants/timeouts are
logged for the critical-priority legs (T2's carry, T6's response, and
R1's K1-recovery leg) along the way.

## Demo B: fog grounds HELI_1; H1 loses receiving capacity

```powershell
rescueflow event helicopter-grounded --vehicle HELI_1 --duration 3600 --cause fog
rescueflow event hospital-capacity --site H1 --slots '[{"start_s":500,"end_s":900,"capacity":0}]'
rescueflow scenario show   # HELI_1 now weather_grounded; H1 shows the new window
rescueflow sim advance --steps 3700   # (if a live session is running) HELI_1 auto-restores to available
```

Both event types are now fully executed (typed + validated + applied),
not just modeled: `HELI_1`'s status becomes `weather_grounded` and a
`ScheduledRestoration` auto-returns it to `available` once `end_s`
passes (`events.apply.tick_scheduled_restorations`, ticked by every `sim
advance`); H1's receiving slots are replaced for the affected window, and
`DefaultPlanValidator._check_receiving_capacity` already enforces it for
any new proposal. See `tests/test_events_prompt2.py` for both paths
covered without needing a live SUMO session.

## Demo C: a live road closure

```powershell
rescueflow sim closure --edge A0A1 --duration 500
```

Disallows `A0A1`'s lanes in the running SUMO session (vehicles already on
it are not ejected or teleported) and records the closure in `WorldState`;
`SumoTravelTimeProvider` then routes every subsequent B0/A1 query around
it identically, with zero planner-side code change (see
docs/CONTRACTS.md "Road closures and scheduled auto-restoration"). Verified
in `tests/test_sumo_execution.py::test_live_road_closure_blocks_routing`.
