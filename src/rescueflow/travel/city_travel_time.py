"""Travel times for a city-sized network: one search per origin, then lookups.

The grid provider searches once per pair of edges, which is too slow on a few
thousand edges. Costs are defined the same way; only ties may resolve
differently, so recorded benchmark runs keep using `SumoTravelTimeProvider`.
"""

from __future__ import annotations

import heapq
import math

from rescueflow.travel.sumo_travel_time import SumoTravelTimeProvider


class CityTravelTimeProvider(SumoTravelTimeProvider):
    def __init__(self, net_path: str) -> None:
        super().__init__(net_path)
        self._next = {e.getID(): tuple(n.getID() for n in e.getOutgoing()) for e in self.net.getEdges()}
        self._trees: dict[tuple, tuple[dict, dict]] = {}
        self._cost: dict[str, float] | None = None

    def set_live_travel_times(self, seconds_by_edge: dict[str, float]) -> None:
        super().set_live_travel_times(seconds_by_edge)
        self._trees.clear()
        self._cost = None

    def set_closed_edges(self, edge_ids) -> None:
        if frozenset(edge_ids) != self._closed:
            self._trees.clear()
        super().set_closed_edges(edge_ids)

    def _tree(self, starts: tuple[str, ...], *, start_costs: bool) -> tuple[dict, dict]:
        key = (starts, start_costs)
        if key in self._trees:
            return self._trees[key]
        if self._cost is None:
            self._cost = {e.getID(): self._edge_s(e) for e in self.net.getEdges()}
        cost = self._cost
        dist = {s: (cost[s] if start_costs else 0.0) for s in starts}
        prev: dict[str, str] = {}
        heap = [(d, s) for s, d in dist.items()]
        heapq.heapify(heap)
        done: set[str] = set()
        while heap:
            d, edge = heapq.heappop(heap)
            if edge in done:
                continue
            done.add(edge)
            for following in self._next[edge]:
                if following in self._closed or following in done:
                    continue
                total = d + cost[following]
                if total < dist.get(following, math.inf):
                    dist[following], prev[following] = total, edge
                    heapq.heappush(heap, (total, following))
        self._trees[key] = dist, prev
        return dist, prev

    def _nearest_end(self, tree: tuple[dict, dict], dest_node: str) -> list[str] | None:
        dist, prev = tree
        ends = [e.getID() for e in self._candidate_in_edges(dest_node) if e.getID() in dist]
        if not ends:
            return None
        path = [min(ends, key=lambda e: dist[e])]
        while path[-1] in prev:
            path.append(prev[path[-1]])
        return path[::-1]

    def _best_path(self, origin_node: str, dest_node: str) -> list | None:
        key = (origin_node, dest_node, self._closed)
        if key not in self._path_cache:
            starts = tuple(e.getID() for e in self._candidate_out_edges(origin_node))
            path = self._nearest_end(self._tree(starts, start_costs=True), dest_node) if starts else None
            self._path_cache[key] = None if path is None else [self.net.getEdge(e) for e in path]
        return self._path_cache[key]

    def path_edges_from_edge(self, from_edge_id: str, dest_node: str) -> list[str] | None:
        return self._nearest_end(self._tree((from_edge_id,), start_costs=False), dest_node)
