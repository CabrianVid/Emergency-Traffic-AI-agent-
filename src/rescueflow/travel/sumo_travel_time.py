"""Road/air ETA estimates for the planner.

Road ETAs are computed on the *same* SUMO network graph used by the
simulator (free-flow length/speed shortest path), not a separate routing
service. This is an explicit interim approximation: it ignores live
congestion and signal delay. Stage 2 is expected to refresh estimates from
live TraCI `simulation.findRoute` / edge occupancy; see docs/CONTRACTS.md.

Air ETAs use the analytical model from the development plan:
    T = activation_s + takeoff_landing_s + great_circle_or_cartesian_distance / 200kmh
This is never SUMO-simulated and must not be presented as road-validated.
"""

from __future__ import annotations

import math
from functools import lru_cache

from rescueflow.sumo.env import ensure_sumolib_importable

HELICOPTER_SPEED_MPS = 200_000 / 3600  # 200 km/h
DEFAULT_ACTIVATION_S = 180.0
DEFAULT_TAKEOFF_LANDING_S = 120.0


class SumoTravelTimeProvider:
    """TravelTimeProvider backed by a static SUMO .net.xml file."""

    def __init__(
        self,
        net_path: str,
        activation_s: float = DEFAULT_ACTIVATION_S,
        takeoff_landing_s: float = DEFAULT_TAKEOFF_LANDING_S,
    ) -> None:
        ensure_sumolib_importable()
        import sumolib  # noqa: PLC0415 -- deliberately lazy, see env.py

        self.net_path = net_path
        self.net = sumolib.net.readNet(net_path)
        self.activation_s = activation_s
        self.takeoff_landing_s = takeoff_landing_s

    def _representative_out_edge(self, node_id: str):
        node = self.net.getNode(node_id)
        outs = node.getOutgoing()
        if outs:
            return outs[0]
        ins = node.getIncoming()
        if ins:
            return ins[0]
        raise ValueError(f"node {node_id!r} has no connecting edges")

    def _representative_in_edge(self, node_id: str):
        node = self.net.getNode(node_id)
        ins = node.getIncoming()
        if ins:
            return ins[0]
        outs = node.getOutgoing()
        if outs:
            return outs[0]
        raise ValueError(f"node {node_id!r} has no connecting edges")

    @lru_cache(maxsize=4096)
    def _shortest_path_time_s(self, origin_node: str, dest_node: str) -> float | None:
        if origin_node == dest_node:
            return 0.0
        from_edge = self._representative_out_edge(origin_node)
        to_edge = self._representative_in_edge(dest_node)
        path, _length_cost = self.net.getShortestPath(from_edge, to_edge)
        if not path:
            return None
        return sum(e.getLength() / e.getSpeed() for e in path)

    def road_eta_s(self, origin_node: str, dest_node: str, depart_s: float) -> float:
        travel_time = self._shortest_path_time_s(origin_node, dest_node)
        if travel_time is None:
            raise ValueError(f"no road route from {origin_node!r} to {dest_node!r}")
        return travel_time

    def road_route_exists(self, origin_node: str, dest_node: str) -> bool:
        return self._shortest_path_time_s(origin_node, dest_node) is not None

    def air_eta_s(self, origin_node: str, dest_node: str) -> float:
        if origin_node == dest_node:
            return self.activation_s + self.takeoff_landing_s
        n1 = self.net.getNode(origin_node)
        n2 = self.net.getNode(dest_node)
        x1, y1 = n1.getCoord()
        x2, y2 = n2.getCoord()
        distance_m = math.hypot(x2 - x1, y2 - y1)
        return self.activation_s + self.takeoff_landing_s + distance_m / HELICOPTER_SPEED_MPS
