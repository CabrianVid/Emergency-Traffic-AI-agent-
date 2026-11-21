"""RescueFlow CLI (docs/RUNBOOK.md has the full command reference).

Every command here is a thin wrapper around the library code under
`rescueflow.*` -- the CLI holds no planning/validation logic of its own.
State is passed between invocations as plain JSON files under
`simulation/scenario/` and `simulation/state/` so each command can be run,
inspected and re-run independently (and so a human can open the JSON and see
exactly what the system believes).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from rescueflow.approval import DefaultApprovalService
from rescueflow.dispatch import MockDispatchNotificationAdapter
from rescueflow.events import EventParseError, RuleBasedEventInterpreter, ValidationError, apply_event
from rescueflow.events.types import EventDraft
from rescueflow.models.core import EventType, PlanProposal, ReportedBy, WorldState
from rescueflow.planning import BaselinePlanner, DefaultPlanValidator, OptimizedPlanner
from rescueflow.planning.solver_log import DEFAULT_SOLVER_LOG_PATH, SolverLog
from rescueflow.scenario.loader import DefaultScenarioLoader, build_initial_world
from rescueflow.sumo.controller import DEFAULT_STATE_PATH, TraciSimulationController
from rescueflow.sumo.network_gen import generate_network, generate_routes, write_sumocfg
from rescueflow.travel import SumoTravelTimeProvider

NET_PATH = "simulation/network/city.net.xml"
ROUTES_PATH = "simulation/network/city.rou.xml"
SUMOCFG_PATH = "simulation/network/city.sumocfg"
SCENARIO_FIXTURE = "simulation/scenario/scenario_v1.json"
WORLD_STATE_PATH = "simulation/state/world.json"
SIM_EVENT_LOG_PATH = "simulation/state/sim_event_log.jsonl"
GREEN_WAVE_FLAG_PATH = "simulation/state/green_wave.json"


def _travel(world: WorldState | None = None) -> SumoTravelTimeProvider:
    if not Path(NET_PATH).exists():
        click.echo(f"Network file {NET_PATH!r} not found. Run 'rescueflow sumo setup' first.", err=True)
        sys.exit(1)
    provider = SumoTravelTimeProvider(NET_PATH)
    if world is not None:
        provider.set_closed_edges(world.closed_edges)
    return provider


def _load_world(path: str) -> WorldState:
    if not Path(path).exists():
        click.echo(f"{path!r} not found. Run 'rescueflow scenario export' / a prior step first.", err=True)
        sys.exit(1)
    return DefaultScenarioLoader().load(path)


def _save_world(world: WorldState, path: str) -> None:
    DefaultScenarioLoader().save(world, path)


def _controller() -> TraciSimulationController:
    return TraciSimulationController(SUMOCFG_PATH, NET_PATH, state_path=DEFAULT_STATE_PATH)


def _append_jsonl(path: str, records: list[dict]) -> None:
    if not records:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


def _green_wave_enabled() -> bool:
    if not Path(GREEN_WAVE_FLAG_PATH).exists():
        return True
    return json.loads(Path(GREEN_WAVE_FLAG_PATH).read_text(encoding="utf-8")).get("enabled", True)


@click.group()
def cli() -> None:
    """RescueFlow AI -- disruption-aware transport replanning (AdaHack 2026)."""


@cli.command()
def doctor() -> None:
    """Check Python/SUMO/git environment; exact remediation on failure."""
    from rescueflow.cli.doctor import print_report, run_all_checks

    ok = print_report(run_all_checks())
    sys.exit(0 if ok else 1)


@cli.group()
def scenario() -> None:
    """Build/inspect the fixed demonstration scenario."""


@scenario.command("export")
@click.option("--out", default=SCENARIO_FIXTURE, show_default=True)
def scenario_export(out: str) -> None:
    """Build the initial 5-ambulance/1-helicopter/7-task scenario and write
    it as stable JSON, validating feasibility before writing."""
    travel = _travel()
    world = build_initial_world(travel)
    report = DefaultScenarioLoader().validate(world)
    if not report.feasible:
        click.echo("Initial scenario is INFEASIBLE -- refusing to export:", err=True)
        for issue in report.issues:
            click.echo(f"  - {issue.code}: {issue.message}", err=True)
        sys.exit(1)
    _save_world(world, out)
    _save_world(world, WORLD_STATE_PATH)
    click.echo(f"Initial scenario is feasible. Wrote {out} and {WORLD_STATE_PATH}.")
    click.echo(f"Vehicles: {sorted(world.vehicles)}")
    click.echo(f"Tasks: {sorted(world.tasks)}")


@scenario.command("show")
@click.option("--path", default=WORLD_STATE_PATH, show_default=True)
def scenario_show(path: str) -> None:
    """Print a summary of a saved world state and re-check feasibility."""
    world = _load_world(path)
    report = DefaultScenarioLoader().validate(world)
    click.echo(f"plan_revision={world.plan_revision} sim_time_s={world.sim_time_s} feasible={report.feasible}")
    for issue in report.issues:
        click.echo(f"  issue: {issue.code}: {issue.message}")
    for v in world.vehicles.values():
        click.echo(f"  vehicle {v.id:8s} mode={v.mode.value:10s} status={v.status.value:10s} stops={len(v.route_stops)}")
    for t in world.tasks.values():
        click.echo(f"  task    {t.id:12s} kind={t.kind.value:20s} priority={t.priority.value:8s} status={t.status.value}")


@cli.group()
def sumo() -> None:
    """Reproducible SUMO network/route generation and smoke testing."""


@sumo.command("setup")
@click.option("--seed", default=42, show_default=True)
@click.option("--amb-start", default="A0", show_default=True)
@click.option("--amb-dest", default="A2", show_default=True,
              help="Kept short (2 hops) so the smoke test's ambulance reliably arrives within 30-120 simulated seconds.")
@click.option("--horizon", default=150.0, show_default=True, help="Simulated seconds covered by the route file / sumocfg.")
def sumo_setup(seed: int, amb_start: str, amb_dest: str, horizon: float) -> None:
    """(Re)generate the 7x7 grid network and a deterministic route file."""
    click.echo("Running netgenerate ...")
    proc = generate_network(NET_PATH, seed=seed)
    click.echo(proc.stdout.strip() or "(no stdout)")
    meta = generate_routes(
        NET_PATH, ROUTES_PATH, seed=seed, amb_start_node=amb_start, amb_dest_node=amb_dest, horizon_s=horizon
    )
    write_sumocfg(NET_PATH, ROUTES_PATH, SUMOCFG_PATH, seed=seed, end_s=horizon)
    click.echo(f"Wrote {NET_PATH}, {ROUTES_PATH}, {SUMOCFG_PATH}")
    click.echo(f"Ambulance route: {amb_start} -> {amb_dest} via {len(meta['ambulance_route_edges'])} edges")
    click.echo(f"Background flows: {len(meta['background_flows'])} (seed={seed})")


@sumo.command("smoke")
@click.option("--duration", default=60, show_default=True, help="Simulated seconds to run.")
def sumo_smoke(duration: int) -> None:
    """Run a real headless SUMO process and confirm the ambulance moves and arrives."""
    if not Path(SUMOCFG_PATH).exists():
        click.echo(f"{SUMOCFG_PATH!r} not found. Run 'rescueflow sumo setup' first.", err=True)
        sys.exit(1)
    controller = TraciSimulationController(SUMOCFG_PATH, NET_PATH)
    result = controller.run_smoke(duration_s=duration)
    click.echo(f"SUMO version: {result.sumo_version}")
    click.echo(f"Wall time: {result.duration_wall_ms:.1f} ms; simulated time elapsed: {result.sim_time_elapsed_s:.1f} s")
    click.echo(f"Detail: {result.detail}")
    click.echo("RESULT: " + ("OK" if result.ok else "FAILED"))
    sys.exit(0 if result.ok else 1)


@cli.group()
def event() -> None:
    """Apply a disruption event to the saved world state."""


@event.command("breakdown")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--text", default="Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.", show_default=True)
def event_breakdown(state: str, text: str) -> None:
    """Parse a free-text breakdown report, validate it against the world,
    and commit it (mutates and saves the world state)."""
    world = _load_world(state)
    interpreter = RuleBasedEventInterpreter()
    try:
        draft = interpreter.parse(text, world.sim_time_s)
    except EventParseError as exc:
        click.echo("AI interpretation unavailable: " + str(exc), err=True)
        sys.exit(1)
    validated = interpreter.validate_event(draft, world)
    if isinstance(validated, ValidationError):
        click.echo(f"Event rejected ({validated.code}): {validated.message}", err=True)
        sys.exit(1)
    apply_event(world, validated)
    _save_world(world, state)
    click.echo(f"Applied {validated.event.type.value} (event_id={validated.event.event_id})")
    click.echo(f"Impact: {validated.event.impact_summary}")
    click.echo(f"plan_revision is now {world.plan_revision}")


@cli.group()
def plan() -> None:
    """Compute and approve repair proposals."""


def _print_proposal(proposal: PlanProposal) -> None:
    click.echo(f"proposal_id={proposal.proposal_id} policy={proposal.policy} profile={proposal.profile}")
    click.echo(f"  feasible={proposal.feasibility.feasible} unserved={proposal.unserved_task_ids}")
    click.echo(f"  score: {proposal.score}")
    click.echo(f"  {proposal.explanation}")
    for issue in proposal.feasibility.issues:
        click.echo(f"  issue: {issue.code}: {issue.message}")
    for vp in proposal.vehicle_plans:
        stops = ", ".join(f"{s.kind.value}:{s.task_id}" for s in vp.stops)
        click.echo(f"  {vp.vehicle_id}: {stops}")


@plan.command("b0")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--out", default="simulation/state/proposal_b0.json", show_default=True)
@click.option("--budget-s", default=2.0, show_default=True, help="Hard wall-clock solve budget.")
def plan_b0(state: str, out: str, budget_s: float) -> None:
    """Compute the B0 (greedy baseline) repair proposal."""
    world = _load_world(state)
    travel = _travel(world)
    planner = BaselinePlanner(travel, solver_log=SolverLog(), solve_budget_s=budget_s)
    proposal = planner.baseline_repair(world, event=world.events[-1] if world.events else None)
    _print_proposal(proposal)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    click.echo(f"Saved to {out}; solver log: {DEFAULT_SOLVER_LOG_PATH}")


@plan.command("a1")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--profiles", default="urgent_first,minimum_disruption", show_default=True)
@click.option("--out-dir", default="simulation/state", show_default=True)
@click.option("--budget-s", default=3.0, show_default=True, help="Hard wall-clock solve budget, per profile.")
def plan_a1(state: str, profiles: str, out_dir: str, budget_s: float) -> None:
    """Compute A1 (fleet-wide optimizer) repair proposals, one per profile."""
    world = _load_world(state)
    travel = _travel(world)
    planner = OptimizedPlanner(travel, solver_log=SolverLog(), solve_budget_s=budget_s)
    profile_list = [p.strip() for p in profiles.split(",") if p.strip()]
    proposals = planner.optimized_repair(world, event=world.events[-1] if world.events else None, profiles=profile_list)
    for proposal in proposals:
        _print_proposal(proposal)
        out_path = Path(out_dir) / f"proposal_a1_{proposal.profile}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        click.echo(f"Saved to {out_path}; solver log: {DEFAULT_SOLVER_LOG_PATH}")


@plan.command("approve")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--proposal", "proposal_path", required=True, type=click.Path(exists=True))
@click.option("--approved-by", default="dispatcher-demo", show_default=True)
@click.option("--idempotency-key", default=None, help="Defaults to the proposal_id.")
def plan_approve(state: str, proposal_path: str, approved_by: str, idempotency_key: str | None) -> None:
    """Approve a saved proposal: revalidates, commits assignments, and logs
    mock dispatch notifications."""
    world = _load_world(state)
    proposal = PlanProposal.model_validate_json(Path(proposal_path).read_text(encoding="utf-8"))
    notifier = MockDispatchNotificationAdapter()
    service = DefaultApprovalService(DefaultPlanValidator(), notifier)
    result = service.approve(world, proposal, approved_by=approved_by, idempotency_key=idempotency_key or proposal.proposal_id)
    if not result.committed:
        click.echo(f"REJECTED: {result.rejection_reason}", err=True)
        sys.exit(1)
    click.echo(f"COMMITTED proposal {result.proposal_id}; plan_revision now {result.new_plan_revision}")
    for msg in result.dispatch_messages:
        click.echo(
            f"  [MOCK DISPATCH] unit={msg.unit_id} task={msg.task_id} dest={msg.destination_label} "
            f"status={msg.delivery_status} plan_version={msg.plan_version}"
        )

    controller = _controller()
    if controller.has_saved_state():
        touched_vehicle_ids = [vp.vehicle_id for vp in proposal.vehicle_plans]
        click.echo(f"Live SUMO session detected -- rerouting {touched_vehicle_ids} in the running simulation...")
        controller.apply_committed_plan(world, touched_vehicle_ids)
        _append_jsonl(SIM_EVENT_LOG_PATH, controller.event_log)
        click.echo("Live vehicles rerouted.")

    _save_world(world, state)
    click.echo(f"Saved updated world to {state}")


# ---------------------------------------------------------------------------
# Purely world-level disruption events (never touch SUMO -- see
# `sim breakdown` / `sim closure` below for the two event types that have a
# genuine physical manifestation in the live simulation).
# ---------------------------------------------------------------------------


def _apply_structured_event(state: str, draft: EventDraft) -> None:
    world = _load_world(state)
    interpreter = RuleBasedEventInterpreter()
    validated = interpreter.validate_event(draft, world)
    if isinstance(validated, ValidationError):
        click.echo(f"Event rejected ({validated.code}): {validated.message}", err=True)
        sys.exit(1)
    apply_event(world, validated)
    _save_world(world, state)
    click.echo(f"Applied {validated.event.type.value} (event_id={validated.event.event_id})")
    click.echo(f"Impact: {validated.event.impact_summary}")
    click.echo(f"plan_revision is now {world.plan_revision}")


@event.command("delay")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--vehicle", "vehicle_id", required=True)
@click.option("--seconds", "delay_s", required=True, type=float)
def event_delay(state: str, vehicle_id: str, delay_s: float) -> None:
    """vehicle_delay: shift a vehicle's remaining (unlocked) stops later."""
    world = _load_world(state)
    draft = EventDraft(type=EventType.vehicle_delay, sim_time_s=world.sim_time_s, vehicle_id=vehicle_id,
                        payload={"delay_s": delay_s}, reported_by=ReportedBy.crew)
    _apply_structured_event(state, draft)


@event.command("priority")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--task", "task_id", required=True)
@click.option("--new-priority", required=True, type=click.Choice(["critical", "high", "routine"]))
@click.option("--reason", required=True)
def event_priority(state: str, task_id: str, new_priority: str, reason: str) -> None:
    """priority_changed: e.g. a patient's condition worsened."""
    world = _load_world(state)
    draft = EventDraft(type=EventType.priority_changed, sim_time_s=world.sim_time_s, task_id=task_id,
                        payload={"new_priority": new_priority, "reason": reason}, reported_by=ReportedBy.dispatcher)
    _apply_structured_event(state, draft)


@event.command("hospital-capacity")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--site", "site_id", required=True)
@click.option("--slots", required=True, help='JSON list, e.g. \'[{"start_s":500,"end_s":900,"capacity":0}]\'')
def event_hospital_capacity(state: str, site_id: str, slots: str) -> None:
    """hospital_capacity_changed: replace a receiving site's capacity over a window."""
    world = _load_world(state)
    draft = EventDraft(type=EventType.hospital_capacity_changed, sim_time_s=world.sim_time_s,
                        payload={"site_id": site_id, "slots": json.loads(slots)}, reported_by=ReportedBy.dispatcher)
    _apply_structured_event(state, draft)


@event.command("helicopter-grounded")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--vehicle", "vehicle_id", default="HELI_1", show_default=True)
@click.option("--duration", "duration_s", default=3600.0, show_default=True, help="Grounding duration in simulated seconds.")
@click.option("--cause", default="fog", show_default=True)
def event_helicopter_grounded(state: str, vehicle_id: str, duration_s: float, cause: str) -> None:
    """helicopter_grounded: e.g. fog, for `duration_s` simulated seconds
    (auto-restores via WorldState.scheduled_restorations -- see `sim advance`)."""
    world = _load_world(state)
    draft = EventDraft(type=EventType.helicopter_grounded, sim_time_s=world.sim_time_s, vehicle_id=vehicle_id,
                        payload={"end_s": world.sim_time_s + duration_s, "cause": cause}, reported_by=ReportedBy.dispatcher)
    _apply_structured_event(state, draft)


# ---------------------------------------------------------------------------
# Live simulation: load/advance/inject-event/snapshot/toggle-green-wave/export-run
# ---------------------------------------------------------------------------


@cli.group()
def sim() -> None:
    """Control the live, stepping SUMO simulation (docs/ARCHITECTURE.md).

    Each command is a separate process; persistence across them uses
    SUMO's own save-state/load-state (simulation/state/sumo.state.xml.gz),
    not a long-lived Python process -- see the `rescueflow.sumo.controller`
    module docstring."""


@sim.command("load")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
def sim_load(state: str) -> None:
    """Fresh start: spawns every ambulance that already has stops into a
    brand-new SUMO session and saves its state for subsequent `sim`
    commands. Helicopters are never injected into SUMO."""
    world = _load_world(state)
    controller = _controller()
    controller.load_scenario(world)
    _append_jsonl(SIM_EVENT_LOG_PATH, controller.event_log)
    _save_world(world, state)
    click.echo(f"Live SUMO session started from {state}; state saved to {controller.state_path}")


@sim.command("reset")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
def sim_reset(state: str) -> None:
    """Deletes the live SUMO session's saved state and the sim event log,
    and resets the world state back to the checked-in scenario fixture."""
    _controller().reset()
    Path(SIM_EVENT_LOG_PATH).unlink(missing_ok=True)
    world = _load_world(SCENARIO_FIXTURE)
    _save_world(world, state)
    click.echo("Live session reset; world state reset to the initial scenario fixture.")


@sim.command("advance")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--steps", default=10, show_default=True, help="Simulated seconds to advance (step-length is 1s).")
def sim_advance(state: str, steps: int) -> None:
    """Steps the live simulation forward, applying real pickup/dropoff
    dwell completions, asset custody transfers, due scheduled restorations,
    and green-wave signal priority as they occur."""
    world = _load_world(state)
    controller = _controller()
    if not controller.has_saved_state():
        click.echo("No live SUMO session. Run 'rescueflow sim load' first.", err=True)
        sys.exit(1)
    controller.green_wave.enabled = _green_wave_enabled()
    controller.connect(resume=True, world=world)
    snapshot = controller.step(world, n=steps)
    controller.save_and_close()
    _append_jsonl(SIM_EVENT_LOG_PATH, controller.event_log)
    _save_world(world, state)
    click.echo(f"Advanced to sim_time_s={snapshot.sim_time_s:.1f} (telemetry_seq={snapshot.telemetry_seq})")
    for vid, pos in snapshot.vehicle_positions.items():
        click.echo(f"  {vid}: edge={pos['edge']} pos={pos['pos']:.1f}")
    if controller.event_log:
        click.echo(f"{len(controller.event_log)} event(s) logged to {SIM_EVENT_LOG_PATH}:")
        for e in controller.event_log:
            click.echo(f"  {e}")


@sim.command("snapshot")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
def sim_snapshot(state: str) -> None:
    """Read-only: prints real vehicle positions and task status from the
    last `sim load`/`advance` (no SUMO connection needed)."""
    world = _load_world(state)
    click.echo(f"sim_time_s={world.sim_time_s:.1f} telemetry_seq={world.telemetry_seq} plan_revision={world.plan_revision}")
    for v in world.vehicles.values():
        if v.sumo_edge_id is not None:
            click.echo(f"  {v.id}: edge={v.sumo_edge_id} pos={v.sumo_lane_position:.1f} status={v.status.value}")
        else:
            click.echo(f"  {v.id}: not live (node={v.node_id}) status={v.status.value}")
    for t in world.tasks.values():
        click.echo(f"  task {t.id}: status={t.status.value}")


@sim.command("breakdown")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--vehicle", "vehicle_id", default="R2", show_default=True)
@click.option("--text", default="Vozilo R2 je obstalo. Komplet K1 je ostal v vozilu.", show_default=True)
def sim_breakdown(state: str, vehicle_id: str, text: str) -> None:
    """Live vehicle_breakdown: physically freezes the vehicle at its real
    live SUMO position *first*, then applies the normal validated event
    using that real position as the recovery task's pickup node."""
    world = _load_world(state)
    controller = _controller()
    if controller.has_saved_state():
        real_node = controller.freeze_vehicle_and_locate(world, vehicle_id)
        _append_jsonl(SIM_EVENT_LOG_PATH, controller.event_log)
        click.echo(f"Physically froze {vehicle_id} at real node {real_node}")
    interpreter = RuleBasedEventInterpreter()
    try:
        draft = interpreter.parse(text, world.sim_time_s)
    except EventParseError as exc:
        click.echo("AI interpretation unavailable: " + str(exc), err=True)
        sys.exit(1)
    validated = interpreter.validate_event(draft, world)
    if isinstance(validated, ValidationError):
        click.echo(f"Event rejected ({validated.code}): {validated.message}", err=True)
        sys.exit(1)
    apply_event(world, validated)
    _save_world(world, state)
    click.echo(f"Applied {validated.event.type.value}; impact: {validated.event.impact_summary}")


@sim.command("closure")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--edge", "edge_id", required=True)
@click.option("--duration", "duration_s", default=None, type=float, help="Reopens automatically after this many seconds.")
def sim_closure(state: str, edge_id: str, duration_s: float | None) -> None:
    """Live road_closure: disallows the edge's lanes in the running SUMO
    session (vehicles already on it are not ejected) and records the
    closure in WorldState so both B0 and A1 detour around it identically."""
    world = _load_world(state)
    draft = EventDraft(type=EventType.road_closure, sim_time_s=world.sim_time_s,
                        payload={"edge_id": edge_id, "duration_s": duration_s}, reported_by=ReportedBy.dispatcher)
    interpreter = RuleBasedEventInterpreter()
    validated = interpreter.validate_event(draft, world)
    if isinstance(validated, ValidationError):
        click.echo(f"Event rejected ({validated.code}): {validated.message}", err=True)
        sys.exit(1)
    apply_event(world, validated)
    controller = _controller()
    if controller.has_saved_state():
        controller.apply_road_closure_physically(world, edge_id)
        _append_jsonl(SIM_EVENT_LOG_PATH, controller.event_log)
    _save_world(world, state)
    click.echo(f"Closed {edge_id}; impact: {validated.event.impact_summary}")


@sim.command("toggle-green-wave")
@click.option("--on/--off", "enabled", default=True)
def sim_toggle_green_wave(enabled: bool) -> None:
    """Enables/disables the green-wave controller for subsequent `sim advance` calls."""
    Path(GREEN_WAVE_FLAG_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(GREEN_WAVE_FLAG_PATH).write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
    click.echo(f"Green wave {'enabled' if enabled else 'disabled'}.")


@sim.command("export-run")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--out", default="simulation/state/run_export.json", show_default=True)
def sim_export_run(state: str, out: str) -> None:
    """Bundles the current world state, the sim event log, and the solver
    log into one JSON file for later inspection or attaching to a report."""
    world = _load_world(state)
    events = []
    if Path(SIM_EVENT_LOG_PATH).exists():
        events = [json.loads(line) for line in Path(SIM_EVENT_LOG_PATH).read_text(encoding="utf-8").splitlines() if line]
    solver = []
    if Path(DEFAULT_SOLVER_LOG_PATH).exists():
        solver = [json.loads(line) for line in Path(DEFAULT_SOLVER_LOG_PATH).read_text(encoding="utf-8").splitlines() if line]
    bundle = {"world": json.loads(world.model_dump_json()), "sim_events": events, "solver_log": solver}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    click.echo(f"Exported {len(events)} sim event(s) and {len(solver)} solver log entrie(s) to {out}")


if __name__ == "__main__":
    cli()
