"""Monte Carlo scenarios: everything exogenous is drawn before any policy runs.

`prepare(root, seed)` writes one frozen scenario (fleet, tasks, civilian trips
and timed unexpected events). All policies replay the same files.
"""

from __future__ import annotations

import json
import random
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from rescueflow.benchmark.inputs import APPROVAL_S, MOBILIZATION_S, digest, frozen_departures, write_json
from rescueflow.models.core import (
    Asset,
    AssetLocation,
    ReceivingSite,
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehicleMode,
    VehicleStatus,
    WorldState,
)
from rescueflow.scenario.loader import DefaultScenarioLoader, _two_stop_route
from rescueflow.sumo.network_gen import generate_network, generate_routes, write_sumocfg
from rescueflow.travel import SumoTravelTimeProvider

SCHEMA = "mc-1"
HORIZON_S = 1800
LAST_CALL_S = 900  # every call can be followed for at least RESPONSE_CAP_S
RESPONSE_CAP_S = 900
RESPONSE_TARGET_S = 300
DENSITY = {"light": 40, "moderate": 22, "heavy": 13}
GRID = [f"{column}{row}" for column in "ABCDEFG" for row in range(7)]


def _events(rng: random.Random, nodes: list[str], hospitals: list[str], units: list[str], edges: list[str]) -> list[dict]:
    """Timed unexpected events. Targets are fixed here; a policy cannot change them."""
    events = []
    calls = rng.randint(2, 6)
    for index in range(1, calls + 1):
        at = rng.randint(60, LAST_CALL_S)
        critical = rng.random() < 0.6
        pickup = rng.choice([n for n in nodes if n not in hospitals])
        events.append({"at": at, "type": "new_urgent_task", "payload": {
            "task_id": f"CALL-{index}", "pickup_node": pickup, "dropoff_node": rng.choice(hospitals),
            "priority": "critical" if critical else "high",
            "required_capabilities": ["ALS"] if critical and rng.random() < 0.5 else [],
            "latest_pickup_s": at + RESPONSE_TARGET_S, "latest_delivery_s": at + 1050,
        }})
    reports = list(events)

    def after_call() -> tuple[int, dict]:
        """A follow-up can only concern a call that has already been reported."""
        call = rng.choice(reports)
        return call["at"] + rng.randint(40, 240), call["payload"]

    if rng.random() < 0.35:
        events.append({"at": rng.randint(90, 800), "type": "vehicle_breakdown", "vehicle_id": rng.choice(units)})
    if rng.random() < 0.35:
        events.append({"at": rng.randint(60, 900), "type": "vehicle_delay", "vehicle_id": rng.choice(units),
                       "payload": {"delay_s": rng.choice([60, 120, 180])}})
    if rng.random() < 0.35:
        at, call = after_call()
        events.append({"at": at, "type": "priority_changed", "task_id": call["task_id"],
                       "payload": {"new_priority": "high" if call["priority"] == "critical" else "critical",
                                   "reason": "reassessment"}})
    if rng.random() < 0.30:
        at, call = after_call()
        other = next(h for h in hospitals if h != call["dropoff_node"])
        events.append({"at": at, "type": "destination_changed", "task_id": call["task_id"],
                       "payload": {"new_dropoff_node": other, "reason": "hospital changed"}})
    if rng.random() < 0.30:
        events.append({"at": rng.randint(60, 700), "type": "road_closure",
                       "payload": {"edge_id": rng.choice(edges), "duration_s": rng.choice([300, 600])}})
    if rng.random() < 0.30:
        events.append({"at": rng.randint(60, 700), "type": "equipment_request",
                       "payload": {"task_id": "EQ-1", "asset_id": "K2", "dropoff_node": rng.choice(hospitals),
                                   "priority": "high"}})
    return sorted(events, key=lambda e: (e["at"], e["type"], json.dumps(e, sort_keys=True)))


def _world(rng: random.Random, travel, hospitals: list[str]) -> WorldState:
    world = WorldState(sim_time_s=0.0)
    for index, node in enumerate(hospitals, 1):
        world.receiving_sites[f"H{index}"] = ReceivingSite(
            id=f"H{index}", node_id=node, service_handling_s=60.0,
            accepted_task_kinds=list(TaskKind))
    free = [n for n in GRID if n not in hospitals]
    starts = rng.sample(free, 6)
    als = set(rng.sample(range(6), rng.randint(2, 5)))
    for index, node in enumerate(starts):
        world.vehicles[f"R{index + 1}"] = Vehicle(
            id=f"R{index + 1}", mode=VehicleMode.ambulance, node_id=node, patient_capacity=1,
            equipment_capacity_units=2, capabilities=["ALS"] if index in als else ["BLS"])
    if rng.random() < 0.15:
        world.vehicles[f"R{rng.randint(1, 6)}"].status = VehicleStatus.offline
    world.assets["K1"] = Asset(id="K1", location=AssetLocation(kind="depot", ref_id=rng.choice(free)))
    world.assets["K2"] = Asset(id="K2", location=AssetLocation(kind="depot", ref_id=rng.choice(free)))
    ready = [v for v in world.vehicles.values() if v.status == VehicleStatus.available]
    assigned = rng.sample(ready, rng.randint(1, min(4, len(ready))))
    equipment = rng.random() < 0.5
    for index, vehicle in enumerate(assigned):
        kit = equipment and index == 0
        pickup = world.assets["K1"].location.ref_id if kit else rng.choice([n for n in free if n != vehicle.node_id])
        task = Task(
            id="KIT-1" if kit else f"TR-{index + 1}",
            kind=TaskKind.equipment_delivery if kit else TaskKind.patient_transport,
            priority=TaskPriority.high if kit else TaskPriority.routine,
            pickup_node=pickup, dropoff_node=rng.choice(hospitals), created_at_s=0.0,
            latest_delivery_s=float(rng.randint(900, 1700)), patient_slots=0 if kit else 1,
            equipment_units=1 if kit else 0, required_asset_ids=["K1"] if kit else [],
            status=TaskStatus.planned, assigned_vehicle_id=vehicle.id)
        world.tasks[task.id] = task
        vehicle.status = VehicleStatus.assigned
        vehicle.route_stops, _ = _two_stop_route(vehicle, task, travel, 0.0, world.receiving_sites.values())
    return world


def prepare(root: Path, seed: int) -> Path:
    folder = root / "inputs" / f"{seed:04d}"
    if (folder / "manifest.json").exists():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    grid = root / "grid.net.xml"
    if not grid.exists():
        generate_network(str(grid), seed=42)
        # netgenerate embeds a timestamp and local path in a comment.
        grid.write_text(re.sub(r"<!--.*?-->\s*", "", grid.read_text(encoding="utf-8"), flags=re.S), encoding="utf-8")
    rng = random.Random(f"{SCHEMA}:{seed}")
    travel = SumoTravelTimeProvider(str(grid))
    hospitals = rng.sample(["A6", "G0", "A0", "G6", "D3"], 2)
    world = _world(rng, travel, hospitals)
    feasibility = DefaultScenarioLoader().validate(world)
    if not feasibility.feasible:
        raise ValueError(f"seed {seed}: {[i.message for i in feasibility.issues]}")
    # Interior roads only: closing one never isolates a corner junction.
    inner = {f"{c}{r}" for c in "BCDEF" for r in range(1, 6)}
    edges = sorted(e.getID() for e in travel.net.getEdges()
                   if e.getFromNode().getID() in inner and e.getToNode().getID() in inner)
    events = _events(rng, GRID, hospitals, list(world.vehicles), edges)
    density = rng.choice(list(DENSITY))
    border = [n for n in GRID if n[0] in "AG" or n[1] in "06"]
    flows = []
    for _ in range(rng.randint(5, 7)):
        a, b = rng.sample(border, 2)
        flows.append((a, b, DENSITY[density] + rng.randint(-3, 3)))
    # SUMO aborts if a pre-planned civilian route enters a closed edge, so
    # civilians avoid this scenario's closure throughout, for every policy.
    travel.set_closed_edges([e["payload"]["edge_id"] for e in events if e["type"] == "road_closure"])
    routes_path = folder / "frozen.rou.xml"
    generate_routes(str(grid), str(routes_path), horizon_s=1200, background_flows=[])
    routes = ET.parse(routes_path).getroot()
    routes.remove(routes.find("vehicle"))
    for vtype in routes.findall("vType"):
        vtype.set("speedFactor", "1")
    ET.SubElement(routes.find("vType[@id='ambulance']"), "param", key="has.bluelight.device", value="true")
    departures = frozen_departures(seed, 1200, flows)
    for depart, identifier, start, end, speed in departures:
        vehicle = ET.SubElement(routes, "vehicle", id=identifier, type="civilian", depart=str(depart),
                                departLane="best", speedFactor=speed)
        ET.SubElement(vehicle, "route", edges=" ".join(travel.road_path_edges(start, end)))
    ET.ElementTree(routes).write(routes_path, encoding="utf-8", xml_declaration=True)
    cfg = folder / "run.sumocfg"
    write_sumocfg(str(grid), str(routes_path), str(cfg), seed=seed, end_s=HORIZON_S)
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('value="grid.net.xml"', 'value="../../grid.net.xml"'),
                   encoding="utf-8")
    world_path = folder / "world.json"
    world_path.write_text(world.model_dump_json(), encoding="utf-8")
    write_json(folder / "manifest.json", {
        "schema": SCHEMA, "scenario": "montecarlo", "seed": seed, "density": density, "horizon_s": HORIZON_S,
        "approval_delay_s": APPROVAL_S, "mobilization_delay_s": MOBILIZATION_S,
        "civilian_departures": len(departures), "events": events,
        "files": {p.name: digest(p) for p in (grid, routes_path, cfg, world_path)},
    })
    return folder
