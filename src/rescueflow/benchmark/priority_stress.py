"""Run paired A1Q/A2Q dispatch through the fixed traffic-suite corridors."""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rescueflow.benchmark.inputs import APPROVAL_S, MOBILIZATION_S, TRAFFIC, digest, write_json
from rescueflow.benchmark.montecarlo import launch
from rescueflow.benchmark.scenarios import HORIZON_S
from rescueflow.models.core import ReceivingSite, TaskKind, Vehicle, VehicleMode, VehicleStatus, WorldState
from rescueflow.sumo.network_gen import write_sumocfg


def freeze(traffic: Path, root: Path, corridor: str, seeds: range) -> None:
    """Keep the civilian inputs and start the same ambulance via actual dispatch."""
    root.mkdir(parents=True, exist_ok=True)
    net = root / "grid.net.xml"
    shutil.copyfile(traffic / "inputs" / "grid.net.xml", net)
    network_hash = digest(net)
    for seed in seeds:
        original = traffic / "inputs" / "traffic" / corridor / str(seed)
        packet = json.loads((original / "manifest.json").read_text(encoding="utf-8"))
        mission = packet["mission"]
        origin, scene = mission["origin"], mission["target"]
        world = WorldState(
            vehicles={"R1": Vehicle(id="R1", mode=VehicleMode.ambulance, node_id=origin,
                                    status=VehicleStatus.available, patient_capacity=1, capabilities=["ALS"])},
            receiving_sites={"H1": ReceivingSite(id="H1", node_id=origin,
                                                 accepted_task_kinds=[TaskKind.urgent_response])},
        )
        folder = root / "inputs" / f"{seed:04}"
        folder.mkdir(parents=True, exist_ok=True)
        routes, config, state = (folder / name for name in ("frozen.rou.xml", "run.sumocfg", "world.json"))
        original_routes = ET.parse(original / routes.name)
        original_routes.getroot().remove(original_routes.find(f"vehicle[@id='{mission['vehicle_id']}']"))
        original_routes.write(routes, encoding="utf-8", xml_declaration=True)
        write_sumocfg(str(net), str(routes), str(config), seed=seed, end_s=HORIZON_S)
        tree = ET.parse(config)
        tree.find("input/net-file").set("value", "../../grid.net.xml")
        tree.write(config, encoding="utf-8", xml_declaration=True)
        state.write_text(world.model_dump_json(), encoding="utf-8")
        report_at = mission["activate_s"] - APPROVAL_S - MOBILIZATION_S
        write_json(folder / "manifest.json", {
            "scenario": corridor, "seed": seed, "density": "heavy" if corridor == "north-peak" else "light",
            "horizon_s": HORIZON_S, "approval_delay_s": APPROVAL_S, "mobilization_delay_s": MOBILIZATION_S,
            "civilian_departures": packet["civilian_departures"], "events": [{
                "at": report_at, "type": "new_urgent_task", "payload": {
                    "task_id": "CALL-1", "pickup_node": scene, "dropoff_node": origin,
                    "priority": "critical", "required_capabilities": ["ALS"],
                    "latest_pickup_s": report_at + 300, "latest_delivery_s": report_at + 1200,
                },
            }],
            "files": {net.name: network_hash, **{p.name: digest(p) for p in (routes, config, state)}},
        })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traffic", type=Path, help="Completed traffic-suite output root")
    parser.add_argument("output", type=Path)
    parser.add_argument("--first-seed", type=int, default=1200)
    parser.add_argument("--scenarios", type=int, default=12)
    parser.add_argument("--jobs", type=int, default=12)
    args = parser.parse_args()
    if args.scenarios < 1 or args.jobs < 2:
        parser.error("Use at least one scenario and two workers.")
    seeds = range(args.first_seed, args.first_seed + args.scenarios)
    directories = [args.output.resolve() / corridor for corridor in TRAFFIC]
    for corridor, directory in zip(TRAFFIC, directories):
        freeze(args.traffic, directory, corridor, seeds)
    with ThreadPoolExecutor(max_workers=len(directories)) as pool:
        jobs = [pool.submit(launch, directory, seeds, ("A1Q", "A2Q"), args.jobs // len(directories))
                for directory in directories]
        for job in jobs:
            job.result()


if __name__ == "__main__":
    main()
