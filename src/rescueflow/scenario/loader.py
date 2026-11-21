"""Builds and (de)serializes the fixed demonstration scenario.

`build_initial_world()` is the single source of truth for the scenario; it
is exported once to `scenario/fixtures/scenario_v1.json` (stable JSON, see
docs/SCENARIOS.md) so every CLI run and every benchmark policy starts from
an identical, reproducible world. All stop ETAs are computed from the
*same* `TravelTimeProvider` used by the planners -- nothing here is a
hardcoded/decorative number.
"""

from __future__ import annotations

import json
from pathlib import Path

from rescueflow.interfaces.protocols import TravelTimeProvider
from rescueflow.models.core import (
    Asset,
    AssetLocation,
    FeasibilityReport,
    PlanProposal,
    ReceivingSite,
    ReceivingSlot,
    ScoreBreakdown,
    Stop,
    StopKind,
    Task,
    TaskKind,
    TaskPriority,
    TaskStatus,
    Vehicle,
    VehicleMode,
    VehicleStatus,
    WorldState,
)
from rescueflow.planning.validator import DefaultPlanValidator

HOSPITAL_H1 = ReceivingSite(
    id="H1",
    node_id="A6",
    accepted_task_kinds=[TaskKind.patient_transport, TaskKind.equipment_delivery, TaskKind.urgent_response],
    slots=[
        ReceivingSlot(start_s=0, end_s=500, capacity=3),
        ReceivingSlot(start_s=500, end_s=900, capacity=0),
        ReceivingSlot(start_s=900, end_s=999_999, capacity=3),
    ],
    service_handling_s=180.0,
)
HOSPITAL_H2 = ReceivingSite(
    id="H2",
    node_id="G0",
    accepted_task_kinds=[TaskKind.patient_transport, TaskKind.equipment_delivery],
    slots=[],
    service_handling_s=150.0,
)


def _two_stop_route(
    vehicle: Vehicle, task: Task, travel: TravelTimeProvider, start_time_s: float,
    receiving_sites=(HOSPITAL_H1, HOSPITAL_H2),
) -> tuple[list[Stop], float]:
    mode = vehicle.mode
    eta_pickup = (
        travel.air_eta_s(vehicle.node_id, task.pickup_node)
        if mode == VehicleMode.helicopter
        else travel.road_eta_s(vehicle.node_id, task.pickup_node, start_time_s)
    )
    pickup_arrival = start_time_s + eta_pickup
    pickup_departure = pickup_arrival + task.load_seconds
    eta_dropoff = (
        travel.air_eta_s(task.pickup_node, task.dropoff_node)
        if mode == VehicleMode.helicopter
        else travel.road_eta_s(task.pickup_node, task.dropoff_node, pickup_departure)
    )
    dropoff_arrival = pickup_departure + eta_dropoff
    handling = next(
        (s.service_handling_s for s in receiving_sites if s.node_id == task.dropoff_node), 0.0
    )
    dropoff_departure = dropoff_arrival + task.unload_seconds + handling

    pickup_stop = Stop(
        stop_id=f"{vehicle.id}-stop-1", vehicle_id=vehicle.id, task_id=task.id, kind=StopKind.pickup,
        node_id=task.pickup_node, arrival_s=pickup_arrival, service_start_s=pickup_arrival,
        departure_s=pickup_departure, earliest_s=task.earliest_pickup_s, latest_s=task.latest_pickup_s,
        patient_load_after=task.patient_slots, equipment_load_after=task.equipment_units, committed=True,
    )
    dropoff_stop = Stop(
        stop_id=f"{vehicle.id}-stop-2", vehicle_id=vehicle.id, task_id=task.id, kind=StopKind.dropoff,
        node_id=task.dropoff_node, arrival_s=dropoff_arrival, service_start_s=dropoff_arrival,
        departure_s=dropoff_departure, earliest_s=task.earliest_delivery_s, latest_s=task.latest_delivery_s,
        patient_load_after=0, equipment_load_after=0, committed=True,
    )
    return [pickup_stop, dropoff_stop], dropoff_departure


def build_initial_world(
    travel: TravelTimeProvider, sim_time_s: float = 120.0, *, node_mapping: dict[str, str] | None = None
) -> WorldState:
    world = WorldState(sim_time_s=sim_time_s)

    world.receiving_sites = {site.id: site.model_copy(deep=True) for site in (HOSPITAL_H1, HOSPITAL_H2)}

    t2 = Task(
        id="T2", kind=TaskKind.patient_transport, priority=TaskPriority.critical,
        pickup_node="D2", dropoff_node="G0", created_at_s=0.0, latest_delivery_s=600.0,
        time_window_hard=True, patient_slots=1, status=TaskStatus.en_route_dropoff, assigned_vehicle_id="R3",
    )
    t3 = Task(
        id="T3", kind=TaskKind.equipment_delivery, priority=TaskPriority.high,
        pickup_node="B1", dropoff_node="A6", created_at_s=0.0, latest_delivery_s=900.0,
        equipment_units=1, required_asset_ids=["K1"], status=TaskStatus.en_route_dropoff, assigned_vehicle_id="R2",
    )
    t5 = Task(
        id="T5", kind=TaskKind.patient_transport, priority=TaskPriority.routine,
        pickup_node="G5", dropoff_node="G0", created_at_s=0.0, latest_delivery_s=2500.0,
        patient_slots=1, status=TaskStatus.planned, assigned_vehicle_id="R5",
    )
    t1 = Task(
        id="T1", kind=TaskKind.patient_transport, priority=TaskPriority.high,
        pickup_node="A1", dropoff_node="A6", created_at_s=0.0, latest_delivery_s=1800.0, patient_slots=1,
    )
    t4 = Task(
        id="T4", kind=TaskKind.equipment_delivery, priority=TaskPriority.routine,
        pickup_node="F5", dropoff_node="G0", created_at_s=0.0, latest_delivery_s=3000.0,
        equipment_units=1, required_asset_ids=["K2"],
    )
    t6 = Task(
        id="T6", kind=TaskKind.urgent_response, priority=TaskPriority.critical,
        pickup_node="C4", dropoff_node="A6", created_at_s=0.0, latest_pickup_s=400.0, latest_delivery_s=900.0,
        patient_slots=1, required_capabilities=["ALS"],
    )
    t7 = Task(
        id="T7", kind=TaskKind.patient_transport, priority=TaskPriority.high,
        pickup_node="E2", dropoff_node="G0", created_at_s=0.0, latest_delivery_s=1500.0, patient_slots=1,
    )

    r1 = Vehicle(id="R1", mode=VehicleMode.ambulance, status=VehicleStatus.available, node_id="A0",
                 patient_capacity=1, equipment_capacity_units=2, capabilities=["ALS"])
    r4 = Vehicle(id="R4", mode=VehicleMode.ambulance, status=VehicleStatus.available, node_id="F5",
                 patient_capacity=1, equipment_capacity_units=2, capabilities=["BLS"])
    heli = Vehicle(id="HELI_1", mode=VehicleMode.helicopter, status=VehicleStatus.available, node_id="D6",
                   patient_capacity=1, equipment_capacity_units=1, capabilities=["ALS"])

    r2 = Vehicle(id="R2", mode=VehicleMode.ambulance, status=VehicleStatus.carrying, node_id="B1",
                 patient_capacity=1, equipment_capacity_units=2, capabilities=["BLS"],
                 equipment_aboard=["K1"], locked_stop_index=1)

    r3 = Vehicle(id="R3", mode=VehicleMode.ambulance, status=VehicleStatus.carrying, node_id="D3",
                 patient_capacity=1, equipment_capacity_units=2, capabilities=["BLS"], locked_stop_index=1)

    r5 = Vehicle(id="R5", mode=VehicleMode.ambulance, status=VehicleStatus.assigned, node_id="G5",
                 patient_capacity=1, equipment_capacity_units=2, capabilities=["BLS"],
                 available_after_s=sim_time_s)

    world.vehicles = {v.id: v for v in (r1, r2, r3, r4, r5, heli)}
    world.tasks = {t.id: t for t in (t1, t2, t3, t4, t5, t6, t7)}
    world.assets = {
        "K1": Asset(id="K1", capacity_units=1, location=AssetLocation(kind="vehicle", ref_id="R2"), status="in_transit"),
        "K2": Asset(id="K2", capacity_units=1, location=AssetLocation(kind="depot", ref_id="F5"), status="available"),
    }
    if node_mapping is not None:
        for site in world.receiving_sites.values():
            site.node_id = node_mapping[site.node_id]
        for vehicle in world.vehicles.values():
            vehicle.node_id = node_mapping[vehicle.node_id]
        for task in world.tasks.values():
            task.pickup_node = node_mapping[task.pickup_node]
            task.dropoff_node = node_mapping[task.dropoff_node]
        world.assets["K2"].location.ref_id = node_mapping["F5"]

    for vehicle, task, start in ((r2, t3, 0.0), (r3, t2, 0.0), (r5, t5, sim_time_s)):
        vehicle.route_stops, _ = _two_stop_route(vehicle, task, travel, start, world.receiving_sites.values())
    return world


class DefaultScenarioLoader:
    """Concrete `ScenarioLoader`."""

    def __init__(self, validator: DefaultPlanValidator | None = None) -> None:
        self.validator = validator or DefaultPlanValidator()

    def load(self, path: str) -> WorldState:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return WorldState.model_validate(data)

    def save(self, world: WorldState, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(world.model_dump_json(indent=2), encoding="utf-8")

    def validate(self, world: WorldState) -> FeasibilityReport:
        probe = PlanProposal(
            proposal_id="initial-state-probe", based_on_plan_revision=world.plan_revision,
            proposed_at_sim_s=world.sim_time_s, policy="b0", feasibility=FeasibilityReport(feasible=True),
            score=ScoreBreakdown(),
        )
        return self.validator.validate(world, probe)
