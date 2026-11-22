"""The fast city provider must cost routes exactly like the grid provider."""

import random

import pytest

from rescueflow.sumo.env import resolve_sumo_env

pytestmark = pytest.mark.skipif(resolve_sumo_env() is None, reason="SUMO is required")


def test_city_provider_matches_the_reference_costs(tmp_path):
    from rescueflow.sumo.network_gen import generate_network
    from rescueflow.travel import SumoTravelTimeProvider
    from rescueflow.travel.city_travel_time import CityTravelTimeProvider

    net = tmp_path / "grid.net.xml"
    generate_network(str(net))
    slow, fast = SumoTravelTimeProvider(str(net)), CityTravelTimeProvider(str(net))
    rng = random.Random(1)
    nodes = sorted(n.getID() for n in slow.net.getNodes())
    edges = sorted(e.getID() for e in slow.net.getEdges())
    live = {e: rng.uniform(5, 120) for e in edges}
    for provider in (slow, fast):
        provider.set_live_travel_times(live)
    for closed in ((), rng.sample(edges, 6)):
        for provider in (slow, fast):
            provider.set_closed_edges(closed)
        for _ in range(25):
            a, b = rng.sample(nodes, 2)
            assert fast.road_eta_s(a, b, 0) == pytest.approx(slow.road_eta_s(a, b, 0))
            start = rng.choice([e for e in edges if e not in closed])
            cost = [sum(p._edge_s(p.net.getEdge(e)) for e in p.path_edges_from_edge(start, b)) for p in (slow, fast)]
            assert cost[0] == pytest.approx(cost[1])
            path = fast.path_edges_from_edge(start, b)
            assert path[0] == start and not set(path) & set(closed)
            assert all(fast.net.getEdge(q) in fast.net.getEdge(p).getOutgoing() for p, q in zip(path, path[1:]))
