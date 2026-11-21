"""Pure-logic tests for the green-wave controller's conflict resolution
(docs/DEVELOPMENT_PLAN.md section 8.2: "two urgent ambulances approaching
incompatible movements must not both receive green"). No SUMO/TraCI
needed -- `_rank_requests` is a plain deterministic function."""

from __future__ import annotations

from rescueflow.sumo.green_wave import GreenWaveController, _Request


def _req(vehicle_id: str, tls_id: str, priority_rank: int, eta_s: float) -> _Request:
    return _Request(
        vehicle_id=vehicle_id, tls_id=tls_id, link_index=0, priority_rank=priority_rank,
        requested_at_s=0.0, eta_s=eta_s,
    )


def test_higher_priority_rank_wins_conflict():
    critical = _req("R1", "J1", priority_rank=0, eta_s=20.0)
    routine = _req("R2", "J1", priority_rank=2, eta_s=5.0)  # closer, but lower priority
    winner = GreenWaveController._rank_requests([critical, routine])[0]
    assert winner.vehicle_id == "R1"


def test_same_priority_ties_broken_by_eta():
    far = _req("R1", "J1", priority_rank=0, eta_s=20.0)
    near = _req("R2", "J1", priority_rank=0, eta_s=5.0)
    winner = GreenWaveController._rank_requests([far, near])[0]
    assert winner.vehicle_id == "R2"


def test_only_one_request_per_junction_is_ever_the_winner():
    reqs = [_req(f"R{i}", "J1", priority_rank=i % 3, eta_s=float(i)) for i in range(5)]
    ranked = GreenWaveController._rank_requests(reqs)
    assert len(ranked) == len(reqs)
    assert ranked[0].priority_rank <= ranked[1].priority_rank

    gw = GreenWaveController()
    for r in reqs:
        gw._requests[(r.vehicle_id, r.tls_id)] = r
    # Only the single best-ranked request may ever be the "winner" for a
    # junction -- the controller must never act on behalf of two at once.
    by_tls: dict[str, list[_Request]] = {}
    for r in gw._requests.values():
        by_tls.setdefault(r.tls_id, []).append(r)
    for tls_id, group in by_tls.items():
        winners = [r for r in group if r is GreenWaveController._rank_requests(group)[0]]
        assert len(winners) == 1


def test_disabled_controller_step_is_a_no_op():
    gw = GreenWaveController(enabled=False)
    gw.step(traci=None, world=None, sim_time_s=0.0, eligible_vehicles={"R1": 0})
    assert gw._requests == {}
    assert gw.log == []
