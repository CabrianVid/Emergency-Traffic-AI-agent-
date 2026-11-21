"""Reproducible SUMO network + deterministic demand generation
(docs/CONTRACTS.md 7.2).

`generate_network` is a thin, logged wrapper around the real `netgenerate`
binary -- it never fabricates a network file. `generate_routes` writes one
frozen route file per seed: the same seed always produces the same
background flows and the same smoke-test ambulance route, computed from the
actual network graph via `sumolib`, not drawn by hand.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rescueflow.sumo.env import ensure_sumolib_importable

GRID_SIZE = 7
GRID_SPACING_M = 400

# A few fixed corner-to-corner background flows: low-regime civilian traffic,
# identical for every run of the same seed because the (origin, destination,
# period) tuples below are hardcoded, not sampled.
_BACKGROUND_FLOWS = [
    ("A0", "G6", 25.0),
    ("G0", "A6", 30.0),
    ("A6", "G0", 35.0),
    ("D0", "D6", 40.0),
]


def prepare_ljubljana_network(source: Path, net: Path) -> None:
    """Build the shared Ljubljana road geometry from the bundled OSM extract."""
    if not source.exists():
        raise FileNotFoundError("Ljubljana OpenStreetMap road extract is missing.")
    if net.exists() and net.stat().st_mtime >= source.stat().st_mtime:
        return
    env = ensure_sumolib_importable()
    netconvert = env.netgenerate_binary.with_name(env.netgenerate_binary.name.replace("netgenerate", "netconvert"))
    net.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(netconvert), "--osm-files", str(source), "--keep-edges.by-vclass", "passenger",
            "--geometry.remove", "--tls.guess-signals", "--tls.yellow.time", "3",
            "--tls.allred.time", "1", "--output.street-names", "true",
            "--output-file", str(net), "--no-warnings", "true",
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )


def generate_network(output_path: str, seed: int = 42) -> subprocess.CompletedProcess:
    from rescueflow.sumo.env import resolve_sumo_env

    env = resolve_sumo_env()
    if env is None:
        raise RuntimeError("SUMO not found; cannot run netgenerate. See docs/RUNBOOK.md.")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(env.netgenerate_binary),
        "--grid",
        "--grid.number", str(GRID_SIZE),
        "--grid.length", str(GRID_SPACING_M),
        "--default.lanenumber", "2",
        "--default.junctions.type", "traffic_light",
        "--tls.yellow.time", "3",
        "--tls.allred.time", "1",
        "--seed", str(seed),
        "--output-file", output_path,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def _edge_path_ids(net, from_node: str, to_node: str) -> list[str]:
    n1 = net.getNode(from_node)
    n2 = net.getNode(to_node)
    from_edge = n1.getOutgoing()[0]
    to_edge = n2.getIncoming()[0]
    path, _ = net.getShortestPath(from_edge, to_edge)
    if not path:
        raise ValueError(f"no route between {from_node} and {to_node}")
    return [e.getID() for e in path]


def generate_routes(
    net_path: str,
    routes_path: str,
    seed: int = 42,
    amb_start_node: str = "A0",
    amb_dest_node: str = "A6",
    amb_depart_s: float = 0.0,
    horizon_s: float = 120.0,
    background_flows=None,
) -> dict:
    """Writes a deterministic route file: a background-traffic vType plus a
    handful of fixed-period corner-to-corner flows, and one `vClass=emergency`
    ambulance vehicle whose route is a real shortest path on the network.
    Returns a small metadata dict describing what was written (for
    docs/IMPLEMENTATION_STATUS.md / the doctor command)."""
    ensure_sumolib_importable()
    import sumolib  # noqa: PLC0415

    net = sumolib.net.readNet(net_path)
    amb_edges = _edge_path_ids(net, amb_start_node, amb_dest_node)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    lines.append('  <vType id="civilian" vClass="passenger" maxSpeed="13.9" accel="2.0" decel="4.5"/>')
    # Two ambulance vTypes, not one: `ambulance` carries SUMO's real
    # vClass="emergency" blue-light privileges (red-light passage, rescue
    # lane) and is used only while a vehicle is actively executing a
    # *critical*-priority leg; `ambulance_routine` is an ordinary vClass
    # with no special privileges, used for every non-critical leg. This is
    # the "emergency-driving fairness" requirement in docs/DECISIONS.md --
    # routine patient/equipment transports never get an unjustified siren,
    # and the comparison between B0/A1 stays apples-to-apples because
    # *both* policies' vehicles switch type by the same rule
    # (`TraciSimulationController._vtype_for_stops`), not by which policy
    # proposed the assignment.
    lines.append('  <vType id="ambulance" vClass="emergency" maxSpeed="20.0" accel="2.5" decel="4.5" '
                  'guiShape="emergency" color="1,0,0"/>')
    lines.append('  <vType id="ambulance_routine" vClass="passenger" maxSpeed="13.9" accel="2.0" decel="4.5" '
                  'guiShape="delivery" color="0,0.4,1"/>')

    flows_written = []
    for i, (origin, dest, period) in enumerate(_BACKGROUND_FLOWS if background_flows is None else background_flows):
        edges = _edge_path_ids(net, origin, dest)
        flow_id = f"bg{i}"
        # A `<route>` child element (not an `edges=` attribute) is required
        # for plain `sumo` -- see docs/DECISIONS.md.
        lines.append(
            f'  <flow id="{flow_id}" type="civilian" begin="0" end="{horizon_s:.0f}" period="{period:.1f}">'
        )
        lines.append(f'    <route edges="{" ".join(edges)}"/>')
        lines.append("  </flow>")
        flows_written.append({"id": flow_id, "from": origin, "to": dest, "period_s": period})

    lines.append(f'  <vehicle id="smoke_amb" type="ambulance" depart="{amb_depart_s:.1f}">')
    lines.append(f'    <route edges="{" ".join(amb_edges)}"/>')
    lines.append("  </vehicle>")
    lines.append("</routes>")

    Path(routes_path).parent.mkdir(parents=True, exist_ok=True)
    Path(routes_path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "seed": seed,
        "ambulance_route_edges": amb_edges,
        "background_flows": flows_written,
    }


def write_sumocfg(net_path: str, routes_path: str, cfg_path: str, seed: int = 42, end_s: float = 120.0) -> None:
    net_rel = Path(net_path).name
    routes_rel = Path(routes_path).name
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="{net_rel}"/>
        <route-files value="{routes_rel}"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="{end_s:.0f}"/>
        <step-length value="1"/>
    </time>
    <processing>
        <seed value="{seed}"/>
    </processing>
    <report>
        <no-step-log value="true"/>
        <duration-log.disable value="true"/>
    </report>
</configuration>
"""
    Path(cfg_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg_path).write_text(xml, encoding="utf-8")
