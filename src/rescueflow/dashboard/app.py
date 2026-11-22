"""FastAPI entry point for the local dispatcher workstation."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rescueflow.benchmark.montecarlo import BUNDLE, ROOT as MC_ROOT, recorded_metrics
from rescueflow.dashboard.agent import DispatcherAgent
from rescueflow.dashboard.service import CommandError, DispatchRun, EventKind, Network, Scenario
from rescueflow.decision.agent import DecisionAgent
from rescueflow.events.types import EventDraft, TaskImpact
from rescueflow.interfaces.protocols import CommitResult
from rescueflow.models.core import DispatchMessage, PlanProposal, Stop, WorldState

log = logging.getLogger(__name__)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoadCommand(Command):
    network: Network = "ljubljana"
    scenario: Scenario = "breakdown"


class StepCommand(Command):
    steps: int = Field(1, ge=1, le=600)


class SpeedCommand(Command):
    speed: Literal[1, 5, 15, 30]


class ReportCommand(Command):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)]


class ScenarioEvent(Command):
    trigger: Literal["scenario"]


class ReportEvent(Command):
    trigger: Literal["report"]
    report_id: str = Field(min_length=1, max_length=80)


class RoadClosureEvent(Command):
    trigger: Literal["road_closure"]
    edge_id: str = Field(min_length=1, max_length=120)
    duration_s: float = Field(240, gt=0, le=3600)


class CallEvent(Command):
    """An emergency logged by the dispatcher, in network metres."""
    trigger: Literal["call"]
    x: float
    y: float
    priority: Literal["critical", "high", "routine"] = "critical"
    needs_als: bool = False
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] = ""


class StructuredEvent(Command):
    trigger: Literal["event"]
    kind: EventKind
    ref: str | None = Field(None, max_length=120)


EventCommand = Annotated[ScenarioEvent | ReportEvent | RoadClosureEvent | StructuredEvent | CallEvent,
                         Field(discriminator="trigger")]


class ApprovalCommand(Command):
    expected_plan_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=120)
    approved_by: str = Field("dispatcher-demo", min_length=1, max_length=100)
    signal_priority: bool = False


class ReplayCommand(Command):
    seed: int = Field(ge=0, le=99999)
    policy: Literal["B0", "B1", "R1", "A1", "A2"]


class AssignmentProjection(BaseModel):
    task_id: str
    vehicle_id: str
    previous_vehicle_id: str | None
    pickup_s: float | None
    delivery_s: float | None
    asset_ids: list[str]


class ProposalView(PlanProposal):
    assignments: list[AssignmentProjection]
    estimated_deadline_misses: list[str]
    routes: dict[str, list[str]]
    equivalent_policies: list[str]
    option_id: str
    strategy: str
    description: str
    rank: int
    recommended: bool
    ai_probability: float | None
    metrics: dict
    signal_priority_units: list[str]
    warnings: list[str]


class ComparisonRow(BaseModel):
    n: int
    average_s: float
    p90_s: float
    run_id: str


class MetricsView(BaseModel):
    civilian_active: int
    civilian_observed: int
    civilian_time_loss_s: float
    responses_s: dict[str, float]
    deadline_delays_s: dict[str, float]
    comparison: dict[str, ComparisonRow]


class DashboardState(BaseModel):
    sequence: int
    run_id: str
    simulator: Literal["paused", "running", "unavailable"]
    demo_pause: bool
    error: str | None
    agent_mode: str
    network: Network
    scenario: Scenario
    speed: int
    script_applied: bool
    world: WorldState | None
    telemetry: dict
    proposals: list[ProposalView]
    proposal_issues: list[str]
    notes: dict[str, str] = Field(default_factory=dict)
    recommendation: dict | None = None
    decision_mode: str = ""
    signal_priority_active: bool = False
    before: dict[str, list[Stop]] | None
    activity: list[dict]
    metrics: MetricsView


class InterpretationView(BaseModel):
    source: str
    valid: bool
    report_id: str | None
    error: str | None = None
    code: str | None = None
    draft: EventDraft | None = None
    summary: str | None = None
    impacted_tasks: list[TaskImpact] = Field(default_factory=list)


def create_app(root: Path | None = None, initial_network: Network | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_dotenv()
        agent = DispatcherAgent()
        base = root or Path("simulation").resolve()
        decision = DecisionAgent(timeout_s=12.0, retries=1, cache_dir=base / "state" / "decision_cache",
                                 log_path=base / "state" / "decision_log.jsonl")
        runtime = DispatchRun(base, agent, decision)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sumo-owner")
        loop = asyncio.get_running_loop()

        async def submit(method, *args):
            return await loop.run_in_executor(executor, method, *args)

        async def play() -> None:
            while True:
                await asyncio.sleep(1)
                with suppress(CommandError):
                    await submit(runtime.tick)

        app.state.runtime = runtime
        app.state.submit = submit
        app.state.replay = None
        app.state.interpret_slots = asyncio.Semaphore(2)
        preferred = initial_network or ("grid" if os.getenv("RESCUEFLOW_NETWORK") == "grid" else "ljubljana")
        try:
            try:
                await submit(runtime.load, preferred, "breakdown")
            except CommandError as exc:
                log.warning("%s", exc)
                if preferred != "grid":
                    with suppress(CommandError):
                        await submit(runtime.load, "grid", "breakdown")
            ticker = asyncio.create_task(play())
            try:
                yield
            finally:
                ticker.cancel()
                with suppress(asyncio.CancelledError):
                    await ticker
        finally:
            try:
                await submit(runtime.close)
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
                await agent.close()

    app = FastAPI(title="RescueFlow AI dispatcher", version="0.3.0", lifespan=lifespan)

    @app.exception_handler(CommandError)
    async def command_error(_, exc: CommandError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/api/state", response_model=DashboardState)
    async def state():
        return app.state.runtime.state

    @app.get("/api/network")
    async def network():
        geometry = app.state.runtime.geometry
        if geometry is None:
            raise HTTPException(503, "The SUMO network is unavailable.")
        return geometry

    @app.post("/api/simulation/load", response_model=DashboardState)
    async def load(command: LoadCommand):
        return await app.state.submit(app.state.runtime.load, command.network, command.scenario)

    @app.post("/api/simulation/reset", response_model=DashboardState)
    async def reset():
        return await app.state.submit(app.state.runtime.reset)

    @app.post("/api/simulation/play", response_model=DashboardState)
    async def play():
        return await app.state.submit(app.state.runtime.control, "play")

    @app.post("/api/simulation/pause", response_model=DashboardState)
    async def pause():
        return await app.state.submit(app.state.runtime.control, "pause")

    @app.post("/api/simulation/step", response_model=DashboardState)
    async def step(command: StepCommand):
        return await app.state.submit(app.state.runtime.control, "step", command.steps)

    @app.post("/api/simulation/speed", response_model=DashboardState)
    async def speed(command: SpeedCommand):
        return await app.state.submit(app.state.runtime.control, "speed", command.speed)

    @app.post("/api/events/interpret", response_model=InterpretationView)
    async def interpret(command: ReportCommand):
        runtime = app.state.runtime
        run_id, world = await app.state.submit(runtime.interpretation_context)
        if app.state.interpret_slots.locked():
            raise HTTPException(429, "Interpretation is busy. Retry or use a scripted scenario.")
        async with app.state.interpret_slots:
            result = await runtime.agent.interpret(command.message, world)
        return await app.state.submit(runtime.check_interpretation, run_id, result)

    @app.post("/api/events", response_model=DashboardState)
    async def inject(command: EventCommand):
        if isinstance(command, ReportEvent):
            return await app.state.submit(app.state.runtime.inject, command.trigger, command.report_id)
        if isinstance(command, CallEvent):
            return await app.state.submit(app.state.runtime.log_call, command.x, command.y, command.priority, command.needs_als, command.note)
        if isinstance(command, RoadClosureEvent):
            return await app.state.submit(app.state.runtime.inject, command.trigger, command.edge_id, command.duration_s)
        if isinstance(command, StructuredEvent):
            return await app.state.submit(app.state.runtime.inject, command.trigger, command.ref, None, command.kind)
        return await app.state.submit(app.state.runtime.inject, command.trigger)

    @app.post("/api/plans/propose", response_model=DashboardState)
    async def propose():
        """Recalculate feasible options and the AI ranking."""
        return await app.state.submit(app.state.runtime.propose)

    @app.post("/api/plans/reject", response_model=DashboardState)
    async def reject():
        return await app.state.submit(app.state.runtime.reject)

    @app.post("/api/plans/{proposal_id}/approve", response_model=CommitResult)
    async def approve(proposal_id: str, command: ApprovalCommand):
        return await app.state.submit(
            app.state.runtime.approve, proposal_id, command.expected_plan_revision,
            command.idempotency_key, command.approved_by, command.signal_priority,
        )

    @app.get("/api/notifications", response_model=list[DispatchMessage])
    async def notifications():
        world = app.state.runtime.state.get("world")
        return world["dispatch_messages"] if world else []

    @app.get("/api/metrics", response_model=MetricsView)
    async def metrics():
        return app.state.runtime.state.get("metrics", {})

    @app.get("/api/benchmarks")
    async def benchmarks():
        configured = os.getenv("RESCUEFLOW_BENCHMARK")
        report = Path(configured) if configured else Path("experiments/benchmark.json")
        if not configured and not report.is_file():
            report = Path("experiments/published/benchmark.json")
        if not report.is_file():
            raise HTTPException(404, "No recorded benchmark report is available.")
        return FileResponse(report, media_type="application/json")

    @app.get("/api/benchmarks/montecarlo")
    async def montecarlo_report():
        report = BUNDLE / "summary.json"
        if not report.is_file():
            raise HTTPException(404, "No Monte Carlo report has been published.")
        return FileResponse(report, media_type="application/json")

    @app.post("/api/benchmarks/montecarlo/replay")
    async def replay_run(command: ReplayCommand):
        """Open a recorded run in the SUMO window. It re-executes the frozen
        inputs with the recorded AI answers; it never calls the AI."""
        running = app.state.replay
        if running is not None and running.poll() is None:
            raise HTTPException(409, "A replay is already open. Close its SUMO window first.")
        try:
            await asyncio.to_thread(recorded_metrics, MC_ROOT, command.seed, command.policy)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        app.state.replay = subprocess.Popen(
            [sys.executable, "-m", "rescueflow.benchmark.montecarlo", "replay",
             "--seed", str(command.seed), "--policy", command.policy],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        return {"started": True, "seed": command.seed, "policy": command.policy}

    @app.websocket("/ws")
    async def snapshots(socket: WebSocket):
        await socket.accept()
        revision = None
        try:
            while True:
                state = app.state.runtime.state
                if revision != state["sequence"]:
                    await socket.send_json(state)
                    revision = state["sequence"]
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(socket.receive_text(), timeout=0.35)
        except WebSocketDisconnect:
            return

    return app


app = create_app()
