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

## Demo A: the central R2/K1 breakdown story

```powershell
rescueflow scenario export
rescueflow event breakdown   # default --text is the exact Slovenian report
rescueflow plan b0
rescueflow plan a1
rescueflow plan approve --proposal simulation/state/proposal_a1_minimum_disruption.json
rescueflow scenario show
```

Observed result in this repo (see docs/IMPLEMENTATION_STATUS.md for the
full run log): R2 is marked broken, T3 is interrupted and superseded by
`T3-RECOVERY` (an auto-generated `equipment_recovery` task whose pickup
node is R2's last confirmed position and whose dropoff node is T3's
original hospital destination), K1's tracked location moves to
`roadside:B1`, and both B0 and A1 find a fully feasible repair (0 unserved
tasks). A1's `minimum_disruption` profile achieves zero critical tardiness
in this run by also relocating T5 off R5 onto R1 -- a genuine, not
pre-scripted, result of the fleet-wide local search; B0 and A1's
`urgent_first` profile both leave some critical tardiness on T6. This is an
honest result, not tuned to make A1 look better -- see
docs/DECISIONS.md and docs/IMPLEMENTATION_STATUS.md for the actual numbers.

## Demo B (helicopter fog / hospital capacity) -- not yet implemented

docs/DEVELOPMENT_PLAN.md section 3.1 describes a second scripted scenario
("Helikopter je zaradi megle eno uro prizemljen; bolnišnica H1 sprejema le
še enega pacienta"). The typed `helicopter_grounded` and
`hospital_capacity_changed` `EventType` values exist in
`models/core.py`, and the validator already enforces a grounded vehicle
correctly (`test_grounded_helicopter_cannot_receive_new_stops`) and
hospital capacity windows (`test_hospital_receiving_capacity_enforced`) --
but `events.apply.apply_event` raises `NotImplementedError` for both event
types; no CLI command drives this scenario end-to-end yet. See
docs/IMPLEMENTATION_STATUS.md "Recommended next prompt".
