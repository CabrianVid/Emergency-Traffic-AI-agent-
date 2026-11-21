"""Prepare the two demo networks and serialize their actual SUMO geometry."""

from __future__ import annotations

from pathlib import Path

from rescueflow.sumo.network_gen import generate_network, generate_routes, prepare_ljubljana_network, write_sumocfg

LJUBLJANA_NODES = {
    "A0": "9561470276", "A1": "33377491", "A6": "9152085335",
    "B1": "10682691046", "C4": "26730347", "D2": "33377578",
    "D3": "5065753786", "D6": "10967896838", "E2": "16202287",
    "F5": "7984232140", "G0": "10052277126", "G5": "4898978348",
    "G6": "33641687",
}

NETWORK_LABELS = {"grid": "Training grid", "ljubljana": "Ljubljana · Tabor & UKC"}


def prepare_network(root: Path, network: str) -> tuple[Path, Path]:
    directory = root / "network" / "dashboard"
    directory.mkdir(parents=True, exist_ok=True)
    net = directory / f"{network}.net.xml"
    routes = directory / f"{network}.rou.xml"
    cfg = directory / f"{network}.sumocfg"
    if network == "grid":
        if not net.exists():
            generate_network(str(net))
        generate_routes(str(net), str(routes), horizon_s=3600)
    else:
        prepare_ljubljana_network(root / "scenario" / "ljubljana-roads.osm.gz", net)
        node = LJUBLJANA_NODES.__getitem__
        generate_routes(
            str(net), str(routes), amb_start_node=node("A0"), amb_dest_node=node("A1"),
            horizon_s=3600,
            background_flows=[
                (node(a), node(b), period)
                for a, b, period in (("A0", "G6", 25), ("G0", "A6", 30), ("A6", "G0", 35), ("E2", "D6", 40))
            ],
        )
    write_sumocfg(str(net), str(routes), str(cfg), end_s=3600)
    return net, cfg


def network_geometry(travel, network: str) -> dict:
    net = travel.net
    return {
        "network": network,
        "label": NETWORK_LABELS[network],
        "bounds": net.getBoundary(),
        "nodes": {node.getID(): node.getCoord() for node in net.getNodes()},
        "edges": {
            edge.getID(): {
                "shape": edge.getShape(includeJunctions=True),
                "name": edge.getName(),
                "lanes": edge.getLaneNumber(),
            }
            for edge in net.getEdges()
        },
        "signals": {
            signal.getID(): signal.getConnections()[0][0].getEdge().getToNode().getCoord()
            for signal in net.getTrafficLights() if signal.getConnections()
        },
    }
