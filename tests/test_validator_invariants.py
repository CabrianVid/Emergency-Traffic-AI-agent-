"""Required invariant tests (prompt 1, "Required tests"): each is a small,
hand-built WorldState + PlanProposal exercising exactly one hard constraint
in `DefaultPlanValidator`, independent of SUMO/travel-time so they always
run. See docs/CONTRACTS.md "Operator/safety invariants".
"""

from __future__ import annotations

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
    Vehicle,
    VehicleMode,
    VehicleStatus,
    VehiclePlan,
    WorldState,
)
from rescueflow.planning.validator import DefaultPlanValidator


def _proposal(vehicle_id: str, stops: list[Stop], world: WorldState) -> PlanProposal:
    return PlanProposal(
        proposal_id="probe", based_on_plan_revision=world.plan_revision, proposed_at_sim_s=world.sim_time_s,
        policy="b0", vehicle_plans=[VehiclePlan(vehicle_id=vehicle_id, stops=stops)],
        feasibility=FeasibilityReport(feasible=True), score=ScoreBreakdown(),
    )


def test_no_incompatible_als_assignment_rejected():
    task = Task(
        id="T_ALS", kind=TaskKind.urgent_response, priority=TaskPriority.critical,
        pickup_node="X", dropoff_node="Y", created_at_s=0.0, required_capabilities=["ALS"],
    )
    bls_vehicle = Vehicle(id="R_BLS", mode=VehicleMode.ambulance, node_id="X", capabilities=["BLS"])
    world = WorldState(vehicles={"R_BLS": bls_vehicle}, tasks={"T_ALS": task})

    stops = [
        Stop(stop_id="s1", vehicle_id="R_BLS", task_id="T_ALS", kind=StopKind.pickup, node_id="X", arrival_s=10),
        Stop(stop_id="s2", vehicle_id="R_BLS", task_id="T_ALS", kind=StopKind.dropoff, node_id="Y", arrival_s=20),
    ]
    report = DefaultPlanValidator().validate(world, _proposal("R_BLS", stops, world))
    assert report.feasible is False
    assert any(i.code == "missing_capability" for i in report.issues)


def test_k1_cannot_be_delivered_before_recovery_and_custody_transfer():
    task = Task(
        id="T_RECOVER", kind=TaskKind.equipment_recovery, priority=TaskPriority.high,
        pickup_node="WRONG_NODE", dropoff_node="H1", created_at_s=0.0, equipment_units=1,
        required_asset_ids=["K1"],
    )
    vehicle = Vehicle(id="R1", mode=VehicleMode.ambulance, node_id="WRONG_NODE", equipment_capacity_units=1)
    k1 = Asset(id="K1", location=AssetLocation(kind="roadside", ref_id="ROADSIDE_NODE"), status="stranded")
    world = WorldState(vehicles={"R1": vehicle}, tasks={"T_RECOVER": task}, assets={"K1": k1})

    bad_stops = [
        Stop(stop_id="s1", vehicle_id="R1", task_id="T_RECOVER", kind=StopKind.pickup, node_id="WRONG_NODE", arrival_s=10),
        Stop(stop_id="s2", vehicle_id="R1", task_id="T_RECOVER", kind=StopKind.dropoff, node_id="H1", arrival_s=20),
    ]
    report = DefaultPlanValidator().validate(world, _proposal("R1", bad_stops, world))
    assert report.feasible is False
    assert any(i.code == "asset_not_at_pickup" for i in report.issues)

    good_stops = [
        Stop(stop_id="s3", vehicle_id="R1", task_id="T_RECOVER", kind=StopKind.pickup, node_id="ROADSIDE_NODE", arrival_s=10),
        Stop(stop_id="s4", vehicle_id="R1", task_id="T_RECOVER", kind=StopKind.dropoff, node_id="H1", arrival_s=20),
    ]
    good_report = DefaultPlanValidator().validate(world, _proposal("R1", good_stops, world))
    assert good_report.feasible, [i.message for i in good_report.issues]


def test_locked_carrying_patient_leg_cannot_be_reassigned():
    task = Task(
        id="T_PATIENT", kind=TaskKind.patient_transport, priority=TaskPriority.critical,
        pickup_node="P", dropoff_node="H2", created_at_s=0.0, patient_slots=1,
    )
    locked_pickup = Stop(stop_id="locked1", vehicle_id="R3", task_id="T_PATIENT", kind=StopKind.pickup,
                          node_id="P", arrival_s=0, departure_s=60, committed=True)
    locked_dropoff = Stop(stop_id="locked2", vehicle_id="R3", task_id="T_PATIENT", kind=StopKind.dropoff,
                           node_id="H2", arrival_s=200, departure_s=260, committed=True)
    vehicle = Vehicle(
        id="R3", mode=VehicleMode.ambulance, node_id="P", patient_capacity=1, status=VehicleStatus.carrying,
        route_stops=[locked_pickup, locked_dropoff], locked_stop_index=1,
    )
    world = WorldState(vehicles={"R3": vehicle}, tasks={"T_PATIENT": task})

    # A candidate plan that drops the in-progress dropoff -- must be rejected.
    tampered_stops = [locked_pickup.model_copy()]
    report = DefaultPlanValidator().validate(world, _proposal("R3", tampered_stops, world))
    assert report.feasible is False
    assert any(i.code == "locked_stop_reassigned" for i in report.issues)

    # Appending a brand-new stop *after* the locked leg is fine.
    new_stop = Stop(stop_id="s_new", vehicle_id="R3", task_id=None, kind=StopKind.standby, node_id="H2", arrival_s=300)
    ok_stops = [locked_pickup.model_copy(), locked_dropoff.model_copy(), new_stop]
    ok_report = DefaultPlanValidator().validate(world, _proposal("R3", ok_stops, world))
    assert ok_report.feasible, [i.message for i in ok_report.issues]


def test_hospital_receiving_capacity_enforced():
    site = ReceivingSite(id="H1", node_id="H1", slots=[ReceivingSlot(start_s=0, end_s=1000, capacity=1)])
    t1 = Task(id="T1", kind=TaskKind.patient_transport, priority=TaskPriority.high, pickup_node="A", dropoff_node="H1",
               created_at_s=0.0, patient_slots=1)
    t2 = Task(id="T2", kind=TaskKind.patient_transport, priority=TaskPriority.high, pickup_node="B", dropoff_node="H1",
               created_at_s=0.0, patient_slots=1)
    v1 = Vehicle(id="V1", mode=VehicleMode.ambulance, node_id="A", patient_capacity=1)
    v2 = Vehicle(id="V2", mode=VehicleMode.ambulance, node_id="B", patient_capacity=1)
    world = WorldState(vehicles={"V1": v1, "V2": v2}, tasks={"T1": t1, "T2": t2}, receiving_sites={"H1": site})

    stops1 = [Stop(stop_id="a1", vehicle_id="V1", task_id="T1", kind=StopKind.pickup, node_id="A", arrival_s=0),
              Stop(stop_id="a2", vehicle_id="V1", task_id="T1", kind=StopKind.dropoff, node_id="H1", arrival_s=100)]
    stops2 = [Stop(stop_id="b1", vehicle_id="V2", task_id="T2", kind=StopKind.pickup, node_id="B", arrival_s=0),
              Stop(stop_id="b2", vehicle_id="V2", task_id="T2", kind=StopKind.dropoff, node_id="H1", arrival_s=150)]
    proposal = PlanProposal(
        proposal_id="probe", based_on_plan_revision=0, proposed_at_sim_s=0.0, policy="b0",
        vehicle_plans=[VehiclePlan(vehicle_id="V1", stops=stops1), VehiclePlan(vehicle_id="V2", stops=stops2)],
        feasibility=FeasibilityReport(feasible=True), score=ScoreBreakdown(),
    )
    report = DefaultPlanValidator().validate(world, proposal)
    assert report.feasible is False
    assert any(i.code == "receiving_capacity_exceeded" for i in report.issues)


def test_grounded_helicopter_cannot_receive_new_stops():
    task = Task(id="T_AIR", kind=TaskKind.urgent_response, priority=TaskPriority.critical,
                pickup_node="A", dropoff_node="B", created_at_s=0.0, patient_slots=1,
                allowed_modes=[VehicleMode.helicopter])
    heli = Vehicle(id="HELI_1", mode=VehicleMode.helicopter, node_id="A", patient_capacity=1,
                    status=VehicleStatus.weather_grounded)
    world = WorldState(vehicles={"HELI_1": heli}, tasks={"T_AIR": task})

    stops = [Stop(stop_id="s1", vehicle_id="HELI_1", task_id="T_AIR", kind=StopKind.pickup, node_id="A", arrival_s=10),
             Stop(stop_id="s2", vehicle_id="HELI_1", task_id="T_AIR", kind=StopKind.dropoff, node_id="B", arrival_s=20)]
    report = DefaultPlanValidator().validate(world, _proposal("HELI_1", stops, world))
    assert report.feasible is False
    assert any(i.code == "unavailable_vehicle_assigned" for i in report.issues)
