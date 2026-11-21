"""Road/air ETA estimates for the planner.

Road ETAs are computed on the *same* SUMO network graph used by the
simulator (free-flow length/speed shortest path), not a separate routing
service. This is an explicit interim approximation: it ignores live
congestion and signal delay. Stage 2 is expected to refresh estimates from
live TraCI `simulation.findRoute` / edge occupancy; see docs/CONTRACTS.md.

Air ETAs use the analytical model from the development plan:
    T = activation_s + takeoff_landing_s + great_circle_or_cartesian_distance / 200kmh
This is never SUMO-simulated and must not be presented as road-validated.

Road closures (`WorldState.closed_edges`) are enforced here with a small
hand-rolled Dijkstra over the network graph (not `sumolib.Net.getShortestPath`,
which has no "exclude these edges" parameter) so that **both** B0 and A1 --
which only ever call `road_eta_s`/`road_route_exists`/`road_path_edges` --
automatically detour around a closed edge identically, with no planner-side
code change. `set_closed_edges` must be called (and clears the ETA cache)
whenever `WorldState.closed_edges` changes -- see
`rescueflow.cli.main._travel` / `docs/CONTRACTS.md`.
"""

from __future__ import annotations

import heapq
import math

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
        self._closed: frozenset[str] = frozenset()
        self._path_cache: dict[tuple[str, str, frozenset[str]], list | None] = {}

    def set_closed_edges(self, edge_ids) -> None:
        """Must be called whenever `WorldState.closed_edges` changes. Does
        not retroactively invalidate ETAs already baked into committed
        `Stop` times -- only affects subsequent planning queries."""
        new_closed = frozenset(edge_ids)
        if new_closed != self._closed:
            self._closed = new_closed
            self._path_cache.clear()

    def _candidate_out_edges(self, node_id: str) -> list:
        node = self.net.getNode(node_id)
        edges = list(node.getOutgoing()) or list(node.getIncoming())
        if not edges:
            raise ValueError(f"node {node_id!r} has no connecting edges")
        return [e for e in edges if e.getID() not in self._closed]

    def _candidate_in_edges(self, node_id: str) -> list:
        node = self.net.getNode(node_id)
        edges = list(node.getIncoming()) or list(node.getOutgoing())
        if not edges:
            raise ValueError(f"node {node_id!r} has no connecting edges")
        return [e for e in edges if e.getID() not in self._closed]

    def _dijkstra(self, from_edge, to_edge_id: str) -> list | None:
        """Shortest (by travel time) edge path from `from_edge` to any edge
        whose id == `to_edge_id`, skipping edges in `self._closed`."""
        if from_edge.getID() == to_edge_id and from_edge.getID() not in self._closed:
            return [from_edge]
        dist = {from_edge: 0.0}
        prev: dict = {}
        visited = set()
        heap = [(0.0, id(from_edge), from_edge)]
        while heap:
            d, _, edge = heapq.heappop(heap)
            if edge in visited:
                continue
            visited.add(edge)
            if edge.getID() == to_edge_id:
                path = [edge]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            for next_edge in edge.getOutgoing():
                if next_edge.getID() in self._closed or next_edge in visited:
                    continue
                weight = next_edge.getLength() / next_edge.getSpeed()
                nd = d + weight
                if next_edge not in dist or nd < dist[next_edge]:
                    dist[next_edge] = nd
                    prev[next_edge] = edge
                    heapq.heappush(heap, (nd, id(next_edge), next_edge))
        return None

    def _best_path(self, origin_node: str, dest_node: str) -> list | None:
        cache_key = (origin_node, dest_node, self._closed)
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]
        best: list | None = None
        best_cost = math.inf
        for from_edge in self._candidate_out_edges(origin_node):
            for to_edge in self._candidate_in_edges(dest_node):
                path = self._dijkstra(from_edge, to_edge.getID())
                if path is None:
                    continue
                cost = sum(e.getLength() / e.getSpeed() for e in path)
                if cost < best_cost:
                    best_cost, best = cost, path
        self._path_cache[cache_key] = best
        return best

    def any_edge_at(self, node_id: str) -> str:
        """One real edge touching `node_id` (prefers an open outgoing edge).
        Used by the controller to attach a `setStop` when a vehicle's first
        stop is at the node it is already parked at."""
        edges = self._candidate_out_edges(node_id)
        if not edges:
            raise ValueError(f"node {node_id!r} has no open departure edge")
        return edges[0].getID()

    def path_edges_from_edge(self, from_edge_id: str, dest_node: str) -> list[str] | None:
        """Like `road_path_edges`, but the path must continue from the
        *specific* edge `from_edge_id` (e.g. the edge a vehicle is already
        committed to driving down), not from any edge touching its origin
        node. Required for stitching multi-stop routes: picking "any"
        candidate departure edge independently per leg can silently chain
        two edges a junction has no actual turn connection between (e.g. an
        immediate U-turn SUMO's generated connections don't allow) -- a
        real bug this was built to fix, see docs/DECISIONS.md. Returns a
        path starting with `from_edge_id` itself."""
        from_edge = self.net.getEdge(from_edge_id)
        best: list | None = None
        best_cost = math.inf
        for to_edge in self._candidate_in_edges(dest_node):
            path = self._dijkstra(from_edge, to_edge.getID())
            if path is None:
                continue
            cost = sum(e.getLength() / e.getSpeed() for e in path)
            if cost < best_cost:
                best_cost, best = cost, path
        return [e.getID() for e in best] if best is not None else None

    def road_path_edges(self, origin_node: str, dest_node: str) -> list[str] | None:
        """Edge-ID path (for building a real SUMO route), or None if every
        path is blocked by a closure. Same graph the planners' ETAs use."""
        if origin_node == dest_node:
            return []
        path = self._best_path(origin_node, dest_node)
        return [e.getID() for e in path] if path is not None else None

    def road_eta_s(self, origin_node: str, dest_node: str, depart_s: float) -> float:
        if origin_node == dest_node:
            return 0.0
        path = self._best_path(origin_node, dest_node)
        if path is None:
            raise ValueError(f"no road route from {origin_node!r} to {dest_node!r} (closures: {sorted(self._closed)})")
        return sum(e.getLength() / e.getSpeed() for e in path)

    def road_route_exists(self, origin_node: str, dest_node: str) -> bool:
        if origin_node == dest_node:
            return True
        return self._best_path(origin_node, dest_node) is not None

    def air_eta_s(self, origin_node: str, dest_node: str) -> float:
        if origin_node == dest_node:
            return self.activation_s + self.takeoff_landing_s
        n1 = self.net.getNode(origin_node)
        n2 = self.net.getNode(dest_node)
        x1, y1 = n1.getCoord()
        x2, y2 = n2.getCoord()
        distance_m = math.hypot(x2 - x1, y2 - y1)
        return self.activation_s + self.takeoff_landing_s + distance_m / HELICOPTER_SPEED_MPS
