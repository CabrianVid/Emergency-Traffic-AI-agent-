"""Freeze all exogenous inputs before launching a policy."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from rescueflow.scenario.loader import DefaultScenarioLoader, build_initial_world
from rescueflow.sumo.network_gen import generate_network, generate_routes, write_sumocfg
from rescueflow.travel import SumoTravelTimeProvider

POLICIES = ("B0", "A1", "A2", "A3")
LOGISTICS = ("breakdown", "urgent", "closure")
TRAFFIC = ("east-low", "north-peak")
APPROVAL_S = 15
MOBILIZATION_S = 10
RESPONSE_TARGET_S = 300
WEIGHTS = {"critical": 10, "high": 4, "routine": 1}
FLOW_ROUTES = (("A0", "G6", 27), ("G0", "A6", 29), ("A6", "G0", 33), ("D0", "D6", 37))
TRAFFIC_MISSIONS = {"east-low": ("A3", "G3", 100), "north-peak": ("D0", "D6", 130)}
SCHEMA_VERSION = 1


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_departures(seed: int, demand_until: int, flow_routes=FLOW_ROUTES, *, peak: bool = False) -> list[tuple]:
    rng = random.Random(seed)
    demand = []
    for index, (start, end, period) in enumerate(flow_routes):
        period = max(5, period // 2) if peak else period
        offset = rng.randrange(period)
        for seq, nominal in enumerate(range(offset, demand_until, period)):
            departure = max(0, nominal + rng.randint(-2, 2))
            demand.append((departure, f"bg{index}-{seq:03}", start, end, f"{rng.uniform(0.9, 1.1):.4f}"))
    return sorted(demand)


def _incidents(scenario: str, seed: int) -> list[dict]:
    if scenario == "breakdown":
        return [
            {"time_s": 151, "type": "vehicle_breakdown", "vehicle_id": "R2", "assets": ["K1"]},
            {
                "type": "new_urgent_task", "after_task": "T3-RECOVERY",
                "payload": {"task_id": "K1-FOLLOWUP", "pickup_node": "A6", "dropoff_node": "G0",
                            "priority": "high", "latest_delivery_s": 2400},
            },
        ]
    if scenario == "closure":
        return [{"time_s": 151, "type": "road_closure", "payload": {"edge_id": "A4A5", "duration_s": 600}}]
    rng = random.Random(seed + 1_000_003)
    first, second = (("C4", "A6"), ("F3", "G0")) if rng.randrange(2) else (("F3", "G0"), ("C4", "A6"))
    return [
        {
            "time_s": at, "type": "new_urgent_task",
            "payload": {
                "task_id": f"CALL-{i}", "pickup_node": origin, "dropoff_node": destination,
                "priority": "critical", "required_capabilities": ["ALS"],
                "latest_pickup_s": at + RESPONSE_TARGET_S, "latest_delivery_s": at + 1050,
            },
        }
        for i, (at, (origin, destination)) in enumerate(((151, first), (181, second)), 1)
    ]


def prepare(root: Path, suite: str, scenario: str, seed: int) -> Path:
    folder = root / "inputs" / suite / scenario / str(seed)
    folder.mkdir(parents=True, exist_ok=True)
    grid = root / "inputs" / "grid.net.xml"
    if not grid.exists():
        generate_network(str(grid), seed=42)
    net = folder / "grid.net.xml"
    shutil.copyfile(grid, net)
    travel = SumoTravelTimeProvider(str(net))
    route_file = folder / "frozen.rou.xml"
    traffic = suite == "traffic"
    horizon = 1000 if traffic else 2800
    demand_until = 540 if traffic else 1500
    generate_routes(str(net), str(route_file), horizon_s=demand_until, background_flows=[])
    routes = ET.parse(route_file).getroot()
    routes.remove(routes.find("vehicle"))
    for vtype in routes.findall("vType"):
        vtype.set("speedFactor", "1")
    ET.SubElement(routes.find("vType[@id='ambulance']"), "param", key="has.bluelight.device", value="true")
    flows = FLOW_ROUTES
    if traffic:
        flows = (*FLOW_ROUTES, ("A3", "G3", 24), ("G3", "A3", 27), ("D6", "D0", 28))
    departures = [
        (time, unit, "civilian", start, end, speed)
        for time, unit, start, end, speed in frozen_departures(seed, demand_until, flows, peak=scenario == "north-peak")
    ]
    mission = None
    if traffic:
        start, end, activate = TRAFFIC_MISSIONS[scenario]
        mission = {"vehicle_id": "priority_probe", "activate_s": activate, "origin": start, "target": end,
                   "edges": travel.road_path_edges(start, end)}
        departures.append((activate, mission["vehicle_id"], "ambulance", start, end, "1"))
    if scenario == "closure":
        # Preserve explicit civilian routes and departures through this
        # experiment; background corridors bypass the closure edge.
        travel.set_closed_edges(["A4A5"])
    for time, identifier, vtype, start, end, speed in sorted(departures):
        vehicle = ET.SubElement(routes, "vehicle", id=identifier, type=vtype,
                                depart=str(time), departLane="best", speedFactor=speed)
        ET.SubElement(vehicle, "route", edges=" ".join(travel.road_path_edges(start, end)))
    travel.set_closed_edges([])
    ET.indent(routes)
    ET.ElementTree(routes).write(route_file, encoding="utf-8", xml_declaration=True)
    cfg = folder / "run.sumocfg"
    write_sumocfg(str(net), str(route_file), str(cfg), seed=seed, end_s=horizon)
    world = build_initial_world(travel)
    if traffic:
        world.vehicles.clear()
        world.tasks.clear()
        world.assets.clear()
        world.receiving_sites.clear()
        world.sim_time_s = 0
    else:
        world.vehicles["R4"].capabilities.append("ALS")
        feasibility = DefaultScenarioLoader().validate(world)
        if not feasibility.feasible:
            raise ValueError(f"Initial logistics schedule is infeasible: {feasibility.issues}")
    state_file = folder / "world.json"
    state_file.write_text(world.model_dump_json(indent=2), encoding="utf-8")
    manifest = {
        "schema": SCHEMA_VERSION, "suite": suite, "scenario": scenario, "seed": seed,
        "network": "training-grid", "horizon_s": horizon, "civilian_departures": len(departures) - int(traffic),
        "approval_delay_s": APPROVAL_S, "mobilization_delay_s": MOBILIZATION_S,
        "response_target_s": RESPONSE_TARGET_S, "weights": WEIGHTS,
        "emergency_device": "SUMO bluelight", "mission": mission,
        "civilian_route_exclusions": ["A4A5"] if scenario == "closure" else [],
        "incidents": [] if traffic else _incidents(scenario, seed),
        "files": {path.name: digest(path) for path in (net, route_file, cfg, state_file)},
    }
    write_json(folder / "manifest.json", manifest)
    return folder
