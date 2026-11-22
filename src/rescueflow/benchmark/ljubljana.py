"""Freeze synthetic scenarios on the real Ljubljana road graph and run Monte Carlo policies."""

from __future__ import annotations

import argparse
import random
import xml.etree.ElementTree as ET
from pathlib import Path

from rescueflow.benchmark.inputs import APPROVAL_S, MOBILIZATION_S, digest, frozen_departures, write_json
from rescueflow.benchmark.montecarlo import ALL_POLICIES, launch, report
from rescueflow.benchmark.scenarios import HORIZON_S, _events, _world
from rescueflow.scenario.loader import DefaultScenarioLoader
from rescueflow.sumo.network_gen import generate_routes, prepare_ljubljana_network, write_sumocfg
from rescueflow.sumo.presentation import CIVILIAN_FLOWS, NETCONVERT_ARGS, SUMO_PROCESSING, resolve_places
from rescueflow.travel.city_travel_time import CityTravelTimeProvider

DEFAULT_POLICIES = ("B0", "B1", "R1Q", "A1Q", "A2Q")
SCHEMA = "mc-ljubljana-1"
DENSITY_MULTIPLIER = {"light": 1.35, "moderate": 1.0, "heavy": 0.70}


def freeze(root: Path, extract: Path, seeds: range) -> None:
    net_path = root / "ljubljana.net.xml"
    prepare_ljubljana_network(extract, net_path, NETCONVERT_ARGS)
    travel = CityTravelTimeProvider(str(net_path))
    places = resolve_places(travel)
    # Large joined intersections can put the stop line more than 30 m from
    # the junction center. Keep call sites within the shared benchmark gate.
    nodes = []
    for key, node_id in places.items():
        if key.startswith("G_") or node_id in nodes:
            continue
        node = travel.net.getNode(node_id)
        x, y = node.getCoord()
        approaches = [
            lane.getShape()[-1] for edge in node.getIncoming() for lane in edge.getLanes()
        ] + [
            lane.getShape()[0] for edge in node.getOutgoing() for lane in edge.getLanes()
        ]
        if approaches and all((a - x) ** 2 + (b - y) ** 2 < 27 ** 2 for a, b in approaches):
            nodes.append(node_id)
    hospitals = [places["UKC"], places["ZD_BEZIGRAD"]]
    if len(set(nodes) - set(hospitals)) < 7:
        raise ValueError("Ljubljana map has too few validated call and station locations.")
    flow_nodes = [(places[a], places[b], period) for a, b, period in CIVILIAN_FLOWS]
    candidates = sorted({
        edge_id for a, b, _ in flow_nodes for edge_id in travel.road_path_edges(a, b)[2:-2]
        if travel.net.getEdge(edge_id).getLength() >= 60
    })
    network_hash = digest(net_path)
    print(f"Ljubljana: {len(nodes)} call/staging nodes, {len(travel.net.getEdges())} road edges", flush=True)
    for seed in seeds:
        folder = root / "inputs" / f"{seed:04d}"
        if (folder / "manifest.json").exists():
            continue
        rng = random.Random(f"{SCHEMA}:{seed}")
        travel.set_closed_edges([])
        world = _world(rng, travel, hospitals, nodes)
        feasible = DefaultScenarioLoader().validate(world)
        if not feasible.feasible:
            raise ValueError(f"Seed {seed}: infeasible initial Ljubljana plan: {feasible.issues}")
        events = _events(rng, nodes, hospitals, list(world.vehicles), candidates)
        density = rng.choice(list(DENSITY_MULTIPLIER))
        flows = [(a, b, round(period * DENSITY_MULTIPLIER[density])) for a, b, period in flow_nodes]
        for event in events:
            if event["type"] != "road_closure":
                continue
            preferred = event["payload"]["edge_id"]
            alternatives = [preferred, *(edge for edge in candidates if edge != preferred)]
            for edge in alternatives:
                travel.set_closed_edges([edge])
                if all(travel.road_route_exists(a, b) for a, b, _ in flows):
                    event["payload"]["edge_id"] = edge
                    break
            else:
                raise ValueError(f"Seed {seed}: no closure preserves all scheduled civilian corridors.")
        departures = frozen_departures(seed, 1200, flows)
        folder.mkdir(parents=True, exist_ok=True)
        routes_path = folder / "frozen.rou.xml"
        config_path = folder / "run.sumocfg"
        world_path = folder / "world.json"
        generate_routes(
            str(net_path), str(routes_path), amb_start_node=flows[0][0], amb_dest_node=flows[0][1],
            background_flows=[], edge_path=travel.road_path_edges,
        )
        routes = ET.parse(routes_path).getroot()
        routes.remove(routes.find("vehicle"))
        for vtype in routes.findall("vType"):
            vtype.set("speedFactor", "1")
        ET.SubElement(routes.find("vType[@id='ambulance']"), "param", key="has.bluelight.device", value="true")
        for depart, vehicle_id, start, end, speed in departures:
            vehicle = ET.SubElement(
                routes, "vehicle", id=vehicle_id, type="civilian", depart=str(depart),
                departLane="best", speedFactor=speed,
            )
            ET.SubElement(vehicle, "route", edges=" ".join(travel.road_path_edges(start, end)))
        ET.ElementTree(routes).write(routes_path, encoding="utf-8", xml_declaration=True)
        write_sumocfg(str(net_path), str(routes_path), str(config_path), seed=seed, end_s=HORIZON_S)
        cfg = ET.parse(config_path)
        cfg.find("input/net-file").set("value", f"../../{net_path.name}")
        for key, value in SUMO_PROCESSING.items():
            ET.SubElement(cfg.find("processing"), key, value=value)
        cfg.write(config_path, encoding="utf-8", xml_declaration=True)
        world_path.write_text(world.model_dump_json(), encoding="utf-8")
        write_json(folder / "manifest.json", {
            "schema": SCHEMA, "scenario": "montecarlo", "network": "ljubljana", "net_file": net_path.name,
            "seed": seed, "density": density, "horizon_s": HORIZON_S,
            "approval_delay_s": APPROVAL_S, "mobilization_delay_s": MOBILIZATION_S,
            "civilian_departures": len(departures), "events": events,
            "files": {net_path.name: network_hash, **{p.name: digest(p) for p in (routes_path, config_path, world_path)}},
        })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--first-seed", type=int, default=1000)
    parser.add_argument("--scenarios", type=int, default=20)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--policies", default=",".join(DEFAULT_POLICIES))
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    policies = tuple(args.policies.split(","))
    if args.scenarios < 1 or args.jobs < 1 or not set(policies) <= set(ALL_POLICIES):
        parser.error("Use positive scenario and job counts and recognized Monte Carlo policies.")
    root = args.root.resolve()
    seeds = range(args.first_seed, args.first_seed + args.scenarios)
    freeze(root, Path("simulation/presentation/ljubljana-wide-roads.osm.gz"), seeds)
    if not args.freeze_only:
        launch(root, seeds, policies, args.jobs)
        report(root)


if __name__ == "__main__":
    main()
