"""Native SUMO presentation of the same Ljubljana mission with B0 or A2 signals."""

from __future__ import annotations

import argparse
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from rescueflow.models.core import WorldState
from rescueflow.sumo.controller import TraciSimulationController
from rescueflow.sumo.env import ensure_sumolib_importable
from rescueflow.sumo.network_gen import prepare_ljubljana_network

SEED = 42
HORIZON_S = 160
AMBULANCE_ID = "AMBULANCE"
DEPARTURE_S = 30
POLICIES = {"B0": "Baseline / normal signals", "A2": "Green wave / signal priority"}
AMBULANCE_MARKER = "ambulance-label"


def write_xml(root: ET.Element, path: Path) -> None:
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def build_routes(net_path: Path, route_path: Path, assets: Path) -> None:
    ensure_sumolib_importable()
    from sumolib.net import readNet

    network = readNet(str(net_path))
    routes = ET.Element("routes")
    for color in ("gold", "mint", "blue"):
        ET.SubElement(routes, "vType", id=f"civilian-{color}", vClass="passenger", maxSpeed="13.9",
                      accel="2.0", decel="4.5", guiShape="passenger", imgFile=str(assets / f"car-{color}.png"))
    ambulance = ET.SubElement(routes, "vType", id="ambulance", vClass="emergency", maxSpeed="20.0",
                             accel="2.5", decel="4.5", color="248,82,72", guiShape="emergency",
                             imgFile=str(assets / "ambulance.png"))
    ET.SubElement(ambulance, "param", key="has.bluelight.device", value="true")

    def path(start: str, target: str, vehicle_class: str) -> str:
        edges, _ = network.getShortestPath(network.getEdge(start), network.getEdge(target), vClass=vehicle_class)
        if not edges:
            raise ValueError(f"No Ljubljana route from {start} to {target} for {vehicle_class}.")
        return " ".join(edge.getID() for edge in edges)

    # Traffic enters from the north, south, east and west of the framed corridor.
    for name, first, last, period, color in (
        ("southbound", "-931184574#4", "867283042#1", 7, "gold"),
        ("northbound", "-867283042#1", "931184574#0", 9, "mint"),
        ("eastbound", "907029154#1", "1116647974#1", 11, "blue"),
        ("westbound", "-1116647974#1", "867283042#1", 10, "gold"),
    ):
        flow = ET.SubElement(routes, "flow", id=name, type=f"civilian-{color}", begin="0", end=str(HORIZON_S),
                             period=str(period), departLane="best")
        ET.SubElement(flow, "route", edges=path(first, last, "passenger"))

    vehicle = ET.SubElement(routes, "vehicle", id=AMBULANCE_ID, type="ambulance", depart=str(DEPARTURE_S),
                            departLane="best", speedFactor="1")
    ET.SubElement(vehicle, "route", edges=path("-931184574#4", "6064800", "emergency"))
    write_xml(routes, route_path)


def prepare_run(assets: Path, net: Path, output: Path, policy: str) -> Path:
    routes = output / "presentation.rou.xml"
    build_routes(net, routes, assets)

    annotations = ET.Element("additional")
    for identifier, label, x, y, color in (
        ("policy", f"{policy}  |  {POLICIES[policy]}", "822", "314", "26,61,72"),
        ("city", "SIMULATION  |  LJUBLJANA", "822", "294", "26,61,72"),
        ("legend", "Red & white ambulance  |  Colored cars", "822", "274", "244,78,72"),
        ("priority", "Signal phases shortened: 0", "822", "254", "26,61,72"),
        ("attribution", "© OpenStreetMap contributors", "1114", "72", "52,100,92"),
        (AMBULANCE_MARKER, "AMBULANCE", "-100", "-100", "244,78,72"),
    ):
        point = ET.SubElement(annotations, "poi", id=identifier, x=x, y=y, color=color,
                              width="3", height="3", layer="5")
        ET.SubElement(point, "param", key="label", value=label)
    labels = output / "labels.add.xml"
    write_xml(annotations, labels)

    cfg = ET.Element("configuration")
    settings = {
        "input": {"net-file": net, "route-files": routes,
                  "additional-files": f"{assets / 'ljubljana.scene.add.xml'},{labels}"},
        "time": {"begin": 0, "end": HORIZON_S, "step-length": 0.5},
        "gui_only": {"gui-settings-file": assets / "ljubljana.view.xml", "window-size": "1240,800"},
    }
    for section, options in settings.items():
        parent = ET.SubElement(cfg, section)
        for name, value in options.items():
            ET.SubElement(parent, name, value=str(value))
    config = output / f"{policy} Ljubljana.sumocfg"
    write_xml(cfg, config)
    return config


def run(root: Path, policy: str, *, gui: bool, output: Path) -> None:
    env = ensure_sumolib_importable()
    os.environ["SUMO_HOME"] = str(env.home)
    assets = root / "simulation" / "presentation"
    net = output / "ljubljana.net.xml"
    prepare_ljubljana_network(root / "simulation" / "scenario" / "ljubljana-roads.osm.gz", net)
    config = prepare_run(assets, net, output, policy)
    controller = TraciSimulationController(str(config), str(net), state_path=str(output / "unused.state.xml.gz"),
                                           seed=SEED, gui=gui, allow_teleport=False, observations_dir=output)
    controller.green_wave.enabled = policy == "A2"
    controller.priority_probe_ids.add(AMBULANCE_ID)
    world = WorldState()
    print(f"RescueFlow | Synthetic Ljubljana SUMO simulation | {policy}: {POLICIES[policy]}", flush=True)
    print(f"Red ambulance is scheduled at {DEPARTURE_S} simulated seconds.", flush=True)
    interventions = 0
    try:
        controller.connect(resume=False, world=world)
        while world.sim_time_s < HORIZON_S:
            event_start = len(controller.event_log)
            controller.step(world, capture_positions=False)
            if gui and world.sim_time_s >= DEPARTURE_S:
                controller.place_gui_label_by_vehicle(AMBULANCE_MARKER, AMBULANCE_ID, (30.0, 8.0))
            for event in controller.event_log[event_start:]:
                if event["kind"] == "phase_shortened":
                    interventions += 1
                    if gui:
                        controller.set_gui_label("priority", f"Signal phases shortened: {interventions}")
    finally:
        controller.close()

    probe = ET.parse(output / "tripinfo.xml").find(f"tripinfo[@id='{AMBULANCE_ID}']")
    restored = sum(event["kind"] == "restored" for event in controller.event_log)
    requests = sum(event["kind"] == "priority_requested" for event in controller.event_log)
    if probe is None or float(probe.get("arrival", "-1")) < 0:
        print("Ambulance did not reach the destination in this run.")
    else:
        print(f"Observed ambulance drive: {probe.get('duration')} s (simulated).")
    print(f"Signal phases shortened: {interventions}. Requests ended: {restored}/{requests}.")
    incidents = sum(event["kind"] in ("collision", "teleport_started") for event in controller.event_log)
    if incidents:
        print(f"SUMO recorded {incidents} collision or teleport events in this run.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", choices=POLICIES, help="B0 for normal signals or A2 for the green wave")
    parser.add_argument("--headless", action="store_true", help="Run the same scene without opening SUMO GUI")
    parser.add_argument("--output", type=Path, help="Directory for SUMO run files. Defaults to a temporary directory.")
    args = parser.parse_args()
    root = Path.cwd()
    if not (root / "simulation" / "presentation").is_dir():
        parser.error("Run this command from the RescueFlow repository root.")
    if args.output:
        run(root, args.policy, gui=not args.headless, output=args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="rescueflow-sumo-") as output:
            run(root, args.policy, gui=not args.headless, output=Path(output))


if __name__ == "__main__":
    main()
