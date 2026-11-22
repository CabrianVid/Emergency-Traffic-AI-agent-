"""Prepare the two demo networks and serialize their actual SUMO geometry."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from rescueflow.sumo.network_gen import generate_network, generate_routes, prepare_ljubljana_network, write_sumocfg
from rescueflow.travel import SumoTravelTimeProvider
from rescueflow.travel.city_travel_time import CityTravelTimeProvider

# Scenario nodes as real places (lat, lon); each becomes the nearest junction
# that can be reached from, and can reach, the first one.
LJUBLJANA_PLACES = {
    "A6": (46.0551, 14.5231),  # H1, UKC
    "G0": (46.0655, 14.5086),  # H2, Bezigrad
    "A0": (46.0536, 14.5199),  # R1, station Center
    "A1": (46.0655, 14.5280),  # T1, Nove Jarse
    "B1": (46.0428, 14.5042),  # R2 with K1, Trnovo
    "C4": (46.0522, 14.5040),  # T6, Center
    "D2": (46.0628, 14.4975),  # T2, Siska
    "D3": (46.0620, 14.4990),  # R3, next to T2
    "E2": (46.0640, 14.5385),  # T7, BTC
    "F5": (46.0532, 14.5368),  # R4 and depot, Moste
    "G5": (46.0452, 14.4968),  # R5 and depot, Vic
    # Simulated incoming calls.
    "CALL1": (46.0560, 14.4985), "CALL2": (46.0470, 14.5400), "CALL3": (46.0416, 14.5196), "CALL4": (46.0572, 14.5168),
    # Where civilian traffic enters and leaves.
    "DUNAJSKA": (46.0672, 14.5112), "CELOVSKA": (46.0628, 14.4925), "TRZASKA": (46.0438, 14.4925),
    "ZALOSKA": (46.0560, 14.5425), "SMARTINSKA": (46.0625, 14.5420), "DOLENJSKA": (46.0420, 14.5150),
    "MASARYKOVA": (46.0583, 14.5105), "SLOVENSKA": (46.0530, 14.5030),
}
CALL_SPOTS = {"ljubljana": ("CALL1", "CALL2", "CALL3", "CALL4"), "grid": ("E2", "C4", "F3", "B5")}
CIVILIAN_FLOWS = (
    ("DUNAJSKA", "TRZASKA", 38), ("TRZASKA", "DUNAJSKA", 39), ("CELOVSKA", "ZALOSKA", 35), ("ZALOSKA", "CELOVSKA", 36),
    ("SMARTINSKA", "SLOVENSKA", 41), ("SLOVENSKA", "SMARTINSKA", 42), ("DOLENJSKA", "MASARYKOVA", 41),
    ("MASARYKOVA", "DOLENJSKA", 42), ("CELOVSKA", "DOLENJSKA", 49), ("DOLENJSKA", "CELOVSKA", 51),
)
# Same settings as the city-wide SUMO scene (docs/INTEGRATIONS.md): they keep
# imported junction clusters from gridlocking.
NETCONVERT_ARGS = ("--tls.join", "--junctions.join", "--roundabouts.guess", "--ramps.guess", "--tls.default-type", "actuated")
SUMO_PROCESSING = {"collision.action": "warn", "collision.check-junctions": "false", "ignore-junction-blocker": "30"}

NETWORK_LABELS = {"grid": "Training grid", "ljubljana": "Ljubljana"}


def reachable_nodes(travel, hub: str) -> list[str]:
    """Junctions with two-way road access from and to `hub`."""
    net = travel.net

    def spread(start, following) -> set:
        seen, stack = set(start), list(start)
        while stack:
            for edge in following(stack.pop()):
                if edge not in seen:
                    seen.add(edge)
                    stack.append(edge)
        return seen

    node = net.getNode(hub)
    forward = spread(node.getOutgoing(), lambda e: e.getOutgoing())
    backward = spread(node.getIncoming(), lambda e: e.getIncoming())

    def usable(n) -> bool:
        ins = [e for e in n.getIncoming() if e in forward and e.getLength() >= 25]
        outs = [e for e in n.getOutgoing() if e in backward and e.getLength() >= 25]
        return bool(ins and outs) and len(n.getIncoming()) + len(n.getOutgoing()) >= 3

    return sorted(n.getID() for n in net.getNodes() if usable(n))


def nearest_node(travel, nodes: list[str], x: float, y: float) -> str:
    def distance(node_id: str) -> float:
        nx, ny = travel.net.getNode(node_id).getCoord()
        return (nx - x) ** 2 + (ny - y) ** 2

    return min(nodes, key=distance)


def prepare_network(root: Path, network: str):
    """Network, SUMO configuration, travel times, named nodes and usable call locations."""
    directory = root / "network" / "dashboard"
    directory.mkdir(parents=True, exist_ok=True)
    if network == "grid":
        net, routes = directory / "grid.net.xml", directory / "grid.rou.xml"
        cfg = directory / "grid.sumocfg"
        if not net.exists():
            generate_network(str(net))
        generate_routes(str(net), str(routes), horizon_s=3600)
        write_sumocfg(str(net), str(routes), str(cfg), end_s=3600)
        travel = SumoTravelTimeProvider(str(net))
        nodes = {n.getID(): n.getID() for n in travel.net.getNodes()}
        return cfg, travel, nodes, sorted(nodes)
    net, routes = directory / "ljubljana-city.net.xml", directory / "ljubljana-city.rou.xml"
    cfg = directory / "ljubljana-city.sumocfg"
    prepare_ljubljana_network(root / "presentation" / "ljubljana-wide-roads.osm.gz", net, NETCONVERT_ARGS)
    travel = CityTravelTimeProvider(str(net))

    def at(key: str, among: list[str]) -> str:
        lat, lon = LJUBLJANA_PLACES[key]
        return nearest_node(travel, among, *travel.net.convertLonLat2XY(lon, lat))

    everywhere = [n.getID() for n in travel.net.getNodes()
                  if n.getIncoming() and n.getOutgoing() and len(n.getIncoming()) + len(n.getOutgoing()) >= 3]
    usable = reachable_nodes(travel, at("A6", everywhere))
    nodes = {key: at(key, usable) for key in LJUBLJANA_PLACES}
    generate_routes(
        str(net), str(routes), amb_start_node=nodes["A0"], amb_dest_node=nodes["A1"], horizon_s=3600,
        background_flows=[(nodes[a], nodes[b], period) for a, b, period in CIVILIAN_FLOWS],
        edge_path=travel.road_path_edges,
    )
    write_sumocfg(str(net), str(routes), str(cfg), end_s=3600)
    tree = ET.parse(cfg)
    processing = tree.getroot().find("processing")
    for option, value in SUMO_PROCESSING.items():
        ET.SubElement(processing, option, value=value)
    tree.write(cfg, encoding="UTF-8", xml_declaration=True)
    return cfg, travel, nodes, usable


def network_geometry(travel, network: str) -> dict:
    net = travel.net
    return {
        "network": network,
        "label": NETWORK_LABELS[network],
        "bounds": net.getBoundary(),
        "utm_offset": net.getLocationOffset(),
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
