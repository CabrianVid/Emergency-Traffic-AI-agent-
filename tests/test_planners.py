"""B0/A1 planning behavior. Uses a tiny fake TravelTimeProvider (no SUMO
dependency) for the capability-over-proximity case, and the real SUMO-backed
scenario fixture (skipped if the network hasn't been generated) for the
full breakdown-repair end-to-end case."""

from __future__ import annotations

from rescueflow.events import RuleBasedEventInterpreter, apply_event
from rescueflow.models.core import (
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehicleMode,
    WorldState,
)
from rescueflow.planning.baseline import BaselinePlanner
from rescueflow.planning.optimized import OptimizedPlanner
from rescueflow.planning.validator import DefaultPlanValidator


class FakeTravelTimeProvider:
    """Straight-line/Manhattan-ish fake ETA: node ids are "x,y" strings."""

    def _coord(self, node: str) -> tuple[float, float]:
        x, y = node.split(",")
        return float(x), float(y)

    def road_eta_s(self, origin_node: str, dest_node: str, depart_s: float) -> float:
        if origin_node == dest_node:
            return 0.0
        ox, oy = self._coord(origin_node)
        dx, dy = self._coord(dest_node)
        return (abs(dx - ox) + abs(dy - oy)) / 10.0  # 10 m/s

    def road_route_exists(self, origin_node: str, dest_node: str) -> bool:
        return True

    def air_eta_s(self, origin_node: str, dest_node: str) -> float:
        return self.road_eta_s(origin_node, dest_node, 0.0)


def test_als_task_never_assigned_to_closer_incapable_vehicle():
    task = Task(id="T_ALS", kind=TaskKind.urgent_response, priority=TaskPriority.critical,
                pickup_node="10,0", dropoff_node="20,0", created_at_s=0.0, patient_slots=1,
                required_capabilities=["ALS"])
    r_bls_close = Vehicle(id="R_BLS", mode=VehicleMode.ambulance, node_id="9,0", patient_capacity=1,
                           capabilities=["BLS"])
    r_als_far = Vehicle(id="R_ALS", mode=VehicleMode.ambulance, node_id="900,0", patient_capacity=1,
                         capabilities=["ALS"])
    world = WorldState(vehicles={"R_BLS": r_bls_close, "R_ALS": r_als_far}, tasks={"T_ALS": task})

    travel = FakeTravelTimeProvider()
    validator = DefaultPlanValidator()
    proposal = BaselinePlanner(travel, validator).baseline_repair(world, event=None)

    assert proposal.feasibility.feasible
    assert "T_ALS" not in proposal.unserved_task_ids
    assigned_vehicle_ids = {vp.vehicle_id for vp in proposal.vehicle_plans}
    assert assigned_vehicle_ids == {"R_ALS"}, "the far ALS-capable vehicle must be chosen, never the closer BLS one"


def test_als_task_unserved_not_hallucinated_when_nobody_qualifies():
    task = Task(id="T_ALS", kind=TaskKind.urgent_response, priority=TaskPriority.critical,
                pickup_node="0,0", dropoff_node="1,0", created_at_s=0.0, patient_slots=1,
                required_capabilities=["ALS"])
    r_bls = Vehicle(id="R_BLS", mode=VehicleMode.ambulance, node_id="0,0", patient_capacity=1, capabilities=["BLS"])
    world = WorldState(vehicles={"R_BLS": r_bls}, tasks={"T_ALS": task})

    travel = FakeTravelTimeProvider()
    proposal = BaselinePlanner(travel).baseline_repair(world, event=None)

    assert proposal.unserved_task_ids == ["T_ALS"]
    assert proposal.vehicle_plans == []


def test_b0_never_touches_unaffected_committed_assignments(world):
    """R5's existing (unlocked but already-planned) T5 assignment must not
    be altered by B0's local repair -- only the newly pending tasks move."""
    interp = RuleBasedEventInterpreter()
    draft = interp.parse("Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.", world.sim_time_s)
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    from rescueflow.travel import SumoTravelTimeProvider

    travel = SumoTravelTimeProvider("simulation/network/city.net.xml")
    proposal = BaselinePlanner(travel).baseline_repair(world, event=validated.event)

    assert proposal.feasibility.feasible, [i.message for i in proposal.feasibility.issues]
    # B0 only ever inserts newly pending/affected tasks -- T5 (already
    # assigned to R5, unaffected by the R2 breakdown) must never be among
    # them, even if B0 later appends a *different* new task onto R5.
    assert "T5" not in proposal.changed_task_ids
    original_t5_stops = [s.stop_id for s in world.vehicles["R5"].route_stops if s.task_id == "T5"]
    for vp in proposal.vehicle_plans:
        if vp.vehicle_id == "R5":
            new_t5_stops = [s.stop_id for s in vp.stops if s.task_id == "T5"]
            assert new_t5_stops == original_t5_stops


def test_a1_can_relocate_unlocked_assigned_task(world):
    """Unlike B0, A1 is allowed to move T5 (assigned to R5 but not yet
    started) off R5 if that improves the fleet-wide objective."""
    interp = RuleBasedEventInterpreter()
    draft = interp.parse("Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.", world.sim_time_s)
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    from rescueflow.travel import SumoTravelTimeProvider

    travel = SumoTravelTimeProvider("simulation/network/city.net.xml")
    proposals = OptimizedPlanner(travel).optimized_repair(
        world, event=validated.event, profiles=["urgent_first", "minimum_disruption"]
    )
    for p in proposals:
        assert p.feasibility.feasible, [i.message for i in p.feasibility.issues]
        assert p.unserved_task_ids == []  # this scenario has a fully feasible repair


def test_breakdown_repair_recovers_k1_before_delivering_it(world):
    interp = RuleBasedEventInterpreter()
    draft = interp.parse("Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.", world.sim_time_s)
    validated = interp.validate_event(draft, world)
    apply_event(world, validated)

    from rescueflow.travel import SumoTravelTimeProvider

    travel = SumoTravelTimeProvider("simulation/network/city.net.xml")
    proposal = BaselinePlanner(travel).baseline_repair(world, event=validated.event)

    recovery_stops = [s for vp in proposal.vehicle_plans for s in vp.stops if s.task_id == "T3-RECOVERY"]
    assert len(recovery_stops) == 2
    pickup = next(s for s in recovery_stops if s.kind.value == "pickup")
    dropoff = next(s for s in recovery_stops if s.kind.value == "dropoff")
    assert pickup.arrival_s < dropoff.arrival_s
