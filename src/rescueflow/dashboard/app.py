"""FastAPI entry point for the local dispatcher workstation."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rescueflow.dashboard.agent import DispatcherAgent
from rescueflow.dashboard.service import PROFILES, CommandError, DispatchRun, Network, Profile, Scenario
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


EventCommand = Annotated[ScenarioEvent | ReportEvent | RoadClosureEvent, Field(discriminator="trigger")]


class ProposeCommand(Command):
    profiles: list[Profile] = Field(default_factory=lambda: list(PROFILES), min_length=1, max_length=3)


class ApprovalCommand(Command):
    expected_plan_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=120)
    approved_by: str = Field("dispatcher-demo", min_length=1, max_length=100)


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
    equivalent_policies: list[Profile]


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
        runtime = DispatchRun(root or Path("simulation").resolve(), agent)
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
        if isinstance(command, RoadClosureEvent):
            return await app.state.submit(app.state.runtime.inject, command.trigger, command.edge_id, command.duration_s)
        return await app.state.submit(app.state.runtime.inject, command.trigger)

    @app.post("/api/plans/propose", response_model=DashboardState)
    async def propose(command: ProposeCommand):
        return await app.state.submit(app.state.runtime.propose, tuple(dict.fromkeys(command.profiles)))

    @app.post("/api/plans/{proposal_id}/approve", response_model=CommitResult)
    async def approve(proposal_id: str, command: ApprovalCommand):
        return await app.state.submit(
            app.state.runtime.approve, proposal_id, command.expected_plan_revision,
            command.idempotency_key, command.approved_by,
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
