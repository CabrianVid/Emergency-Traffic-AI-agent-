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
from rescueflow.models.core import PlanProposal, WorldState
from rescueflow.planning import BaselinePlanner, DefaultPlanValidator, OptimizedPlanner
from rescueflow.scenario.loader import DefaultScenarioLoader, build_initial_world
from rescueflow.sumo.controller import TraciSimulationController
from rescueflow.sumo.network_gen import generate_network, generate_routes, write_sumocfg
from rescueflow.travel import SumoTravelTimeProvider

NET_PATH = "simulation/network/city.net.xml"
ROUTES_PATH = "simulation/network/city.rou.xml"
SUMOCFG_PATH = "simulation/network/city.sumocfg"
SCENARIO_FIXTURE = "simulation/scenario/scenario_v1.json"
WORLD_STATE_PATH = "simulation/state/world.json"


def _travel() -> SumoTravelTimeProvider:
    if not Path(NET_PATH).exists():
        click.echo(f"Network file {NET_PATH!r} not found. Run 'rescueflow sumo setup' first.", err=True)
        sys.exit(1)
    return SumoTravelTimeProvider(NET_PATH)


def _load_world(path: str) -> WorldState:
    if not Path(path).exists():
        click.echo(f"{path!r} not found. Run 'rescueflow scenario export' / a prior step first.", err=True)
        sys.exit(1)
    return DefaultScenarioLoader().load(path)


def _save_world(world: WorldState, path: str) -> None:
    DefaultScenarioLoader().save(world, path)


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
    controller = TraciSimulationController(SUMOCFG_PATH)
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
def plan_b0(state: str, out: str) -> None:
    """Compute the B0 (greedy baseline) repair proposal."""
    world = _load_world(state)
    travel = _travel()
    planner = BaselinePlanner(travel)
    proposal = planner.baseline_repair(world, event=world.events[-1] if world.events else None)
    _print_proposal(proposal)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    click.echo(f"Saved to {out}")


@plan.command("a1")
@click.option("--state", default=WORLD_STATE_PATH, show_default=True)
@click.option("--profiles", default="urgent_first,minimum_disruption", show_default=True)
@click.option("--out-dir", default="simulation/state", show_default=True)
def plan_a1(state: str, profiles: str, out_dir: str) -> None:
    """Compute A1 (fleet-wide optimizer) repair proposals, one per profile."""
    world = _load_world(state)
    travel = _travel()
    planner = OptimizedPlanner(travel)
    profile_list = [p.strip() for p in profiles.split(",") if p.strip()]
    proposals = planner.optimized_repair(world, event=world.events[-1] if world.events else None, profiles=profile_list)
    for proposal in proposals:
        _print_proposal(proposal)
        out_path = Path(out_dir) / f"proposal_a1_{proposal.profile}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        click.echo(f"Saved to {out_path}")


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
    _save_world(world, state)
    click.echo(f"Saved updated world to {state}")


if __name__ == "__main__":
    cli()
