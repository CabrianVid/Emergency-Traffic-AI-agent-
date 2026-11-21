"""Serialized commands and immutable read snapshots for one dashboard run."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from statistics import mean
from typing import Literal
from uuid import uuid4

from rescueflow.approval.service import DefaultApprovalService
from rescueflow.dashboard.agent import DispatcherAgent, Interpretation
from rescueflow.dashboard.network import LJUBLJANA_NODES, network_geometry, prepare_network
from rescueflow.dispatch.notification import MockDispatchNotificationAdapter
from rescueflow.events.apply import apply_event
from rescueflow.events.types import EventDraft, ValidationError
from rescueflow.models.core import EventType, TaskStatus
from rescueflow.planning.baseline import BaselinePlanner
from rescueflow.planning.optimized import OptimizedPlanner, plan_outcome_signature
from rescueflow.planning.solver_log import SolverLog
from rescueflow.scenario.loader import DefaultScenarioLoader, build_initial_world
from rescueflow.sumo.controller import TraciSimulationController
from rescueflow.travel import SumoTravelTimeProvider

Network = Literal["ljubljana", "grid"]
Scenario = Literal["breakdown", "urgent", "fog"]
Profile = Literal["baseline", "urgent_first", "minimum_disruption"]
PROFILES: tuple[Profile, ...] = ("baseline", "urgent_first", "minimum_disruption")
SCRIPTS = {"breakdown": "R2 breakdown · K1 recovery", "urgent": "New urgent patient", "fog": "Fog · receiving capacity"}
log = logging.getLogger(__name__)


class CommandError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class DispatchRun:
    """All methods that touch the run execute on a single worker thread."""

    def __init__(self, root: Path, agent: DispatcherAgent) -> None:
        self.root = root
        self.agent = agent
        self.controller = None
        self.world = None
        self.geometry = None
        self.network: Network = "grid"
        self.scenario: Scenario = "breakdown"
        self.speed = 1
        self.playing = False
        self.error = None
        self.run_id = ""
        self.sequence = 0
        self.state: dict = {"sequence": 0, "simulator": "starting", "agent_mode": agent.label}
        self._comparisons: dict[tuple, dict] = {}

    def load(self, network: Network, scenario: Scenario) -> dict:
        self.close()
        self.world = None
        self.network, self.scenario = network, scenario
        self.run_id = uuid4().hex
        self.speed = 1
        self.playing = False
        self.demo_pause = False
        self.error = None
        self._events: list[dict] = []
        self._event_cursor = 0
        self._reports: dict[str, EventDraft] = {}
        self._proposal_models = {}
        self._proposals: list[dict] = []
        self._issues: list[str] = []
        self._approvals: dict[str, tuple[str, dict]] = {}
        self._before = None
        self._dispatch_times: dict[str, float] = {}
        self._responses: dict[str, float] = {}
        self._delays: dict[str, float] = {}
        self._policy = None
        self._script_applied = False
        self._directory = self.root / "state" / "dashboard" / self.run_id
        try:
            net, cfg = prepare_network(self.root, network)
            self.travel = SumoTravelTimeProvider(str(net))
            world = build_initial_world(
                self.travel, node_mapping=LJUBLJANA_NODES if network == "ljubljana" else None
            )
            feasibility = DefaultScenarioLoader().validate(world)
            if not feasibility.feasible:
                raise ValueError("Invalid initial schedule: " + ", ".join(i.message for i in feasibility.issues))
            self.controller = TraciSimulationController(
                str(cfg), str(net), state_path=str(self._directory / "checkpoint.xml.gz"), allow_teleport=False,
            )
            self.controller.load_scenario(world, keep_connected=True)
            self.controller.step(world, n=129 if network == "ljubljana" else 150)
            self.geometry = network_geometry(self.travel, network)
            self.world = world
            self._record(kind="scenario_loaded", message=SCRIPTS[scenario])
            return self.publish()
        except Exception as exc:
            self.close()
            self.geometry = None
            self.error = f"SUMO could not load {network}: {exc}"
            self.publish()
            raise CommandError(self.error, 503) from exc

    def close(self) -> None:
        if self.controller is not None:
            self.controller.close()
            self.controller = None

    def require_world(self):
        if self.world is None or self.controller is None or self.error:
            raise CommandError(self.error or "Load a SUMO scenario first.", 503)
        return self.world

    def reset(self) -> dict:
        return self.load(self.network, self.scenario)

    def control(self, action: str, value: int | None = None) -> dict:
        self.require_world()
        self.demo_pause = False
        if action == "speed":
            self.speed = value
        elif action == "step":
            self.playing = False
            return self.tick(value)
        else:
            self.playing = action == "play"
        return self.publish()

    def tick(self, steps: int | None = None) -> dict:
        if steps is None and not self.playing:
            return self.state
        world = self.require_world()
        try:
            self.controller.step(world, n=steps or self.speed)
            return self.publish()
        except Exception as exc:
            self.playing = False
            self.error = f"SUMO execution paused: {exc}"
            log.exception("SUMO step failed")
            self.publish()
            raise CommandError(self.error, 503) from exc

    def interpretation_context(self):
        return self.run_id, self.require_world().model_copy(deep=True)

    def check_interpretation(self, run_id: str, result: Interpretation) -> dict:
        world = self.require_world()
        if run_id != self.run_id:
            raise CommandError("The scenario changed. Interpret the report again.")
        response = {"source": result.source, "valid": False, "report_id": None}
        if result.draft is None:
            return {**response, "error": result.error, "code": "needs_confirmation"}
        draft = result.draft.model_copy(update={"sim_time_s": world.sim_time_s})
        validated = self.agent.validate_event(draft, world)
        if isinstance(validated, ValidationError):
            return {**response, "error": validated.message, "code": validated.code, "draft": draft}
        report_id = uuid4().hex
        if len(self._reports) >= 32:
            del self._reports[next(iter(self._reports))]
        self._reports[report_id] = draft
        return {
            **response, "valid": True, "report_id": report_id, "draft": draft,
            "summary": validated.event.impact_summary,
            "impacted_tasks": self.agent.get_impacted_tasks(validated, world),
        }

    def inject(self, trigger: str, ref: str | None = None, duration_s: float | None = None) -> dict:
        world = self.require_world()
        now = world.sim_time_s
        if trigger == "report":
            draft = self._reports.get(ref)
            if draft is None:
                raise CommandError("Interpret the report for this run before applying it.", 404)
            drafts = [draft.model_copy(update={"sim_time_s": now})]
        elif trigger == "road_closure":
            if ref not in self.geometry["edges"]:
                raise CommandError("Select a road edge from the loaded network.", 422)
            drafts = [EventDraft(type=EventType.road_closure, sim_time_s=now,
                                 payload={"edge_id": ref, "duration_s": duration_s})]
        else:
            if self._script_applied:
                raise CommandError("Reset the run to replay its scripted incident.")
            drafts = self._scripted_drafts(now)
        validated = []
        for draft in drafts:
            event = self.agent.validate_event(draft, world)
            if isinstance(event, ValidationError):
                raise CommandError(event.message, 422)
            validated.append(event)
            if draft.type == EventType.vehicle_breakdown:
                edge = world.vehicles[draft.vehicle_id].sumo_edge_id
                if edge and edge.startswith(":"):
                    raise CommandError(f"{draft.vehicle_id} is in a junction. Advance the simulation and retry.")
        self._before = self._schedule()
        self.playing = False
        self.demo_pause = True
        for event in validated:
            impacted = self.agent.get_impacted_tasks(event, world)
            payload = event.event.payload
            if event.event.type == EventType.vehicle_breakdown:
                self.controller.freeze_vehicle_and_locate(world, payload["vehicle_id"])
            elif event.event.type == EventType.road_closure:
                self.controller.apply_road_closure_physically(world, payload["edge_id"])
            apply_event(world, event)
            self._record(kind=event.event.type.value, message=event.event.impact_summary,
                         impacted_tasks=[task.task_id for task in impacted])
        if trigger == "report":
            self._reports.pop(ref, None)
        elif trigger == "scenario":
            self._script_applied = True
        return self.propose(PROFILES)

    def _scripted_drafts(self, now: float) -> list[EventDraft]:
        if self.scenario == "breakdown":
            return [EventDraft(type=EventType.vehicle_breakdown, sim_time_s=now, vehicle_id="R2",
                               equipment_ids_reported_stranded=["K1"])]
        if self.scenario == "urgent":
            pickup = LJUBLJANA_NODES["E2"] if self.network == "ljubljana" else "E2"
            return [EventDraft(type=EventType.new_urgent_task, sim_time_s=now, payload={
                "task_id": "URGENT", "pickup_node": pickup,
                "dropoff_node": self.world.receiving_sites["H2"].node_id,
                "priority": "critical", "latest_pickup_s": now + 240, "latest_delivery_s": now + 950,
            })]
        return [
            EventDraft(type=EventType.helicopter_grounded, sim_time_s=now, vehicle_id="HELI_1",
                       payload={"end_s": now + 3600, "cause": "fog"}),
            EventDraft(type=EventType.hospital_capacity_changed, sim_time_s=now, payload={
                "site_id": "H1", "slots": [{"start_s": max(900, now), "end_s": max(900, now) + 900, "capacity": 1}],
            }),
        ]

    def propose(self, profiles: tuple[Profile, ...]) -> dict:
        world = self.require_world()
        self.playing = False
        self.demo_pause = True
        self.travel.set_closed_edges(world.closed_edges)
        event = world.events[-1] if world.events else None
        solver_log = SolverLog(str(self._directory / "solver.jsonl"))
        candidates = []
        optimized_profiles = [profile for profile in profiles if profile != "baseline"]
        if optimized_profiles:
            candidates.extend(OptimizedPlanner(self.travel, solver_log=solver_log).optimized_repair(
                world, event, optimized_profiles
            ))
        if "baseline" in profiles:
            candidates.append(BaselinePlanner(self.travel, solver_log=solver_log).baseline_repair(world, event))
        self._issues = []
        self._proposals = []
        signatures = {}
        for proposal in candidates:
            if not proposal.feasibility.feasible:
                self._issues.extend(issue.message for issue in proposal.feasibility.issues)
                continue
            try:
                routes = self.controller.preview_routes(world, {p.vehicle_id: p.stops for p in proposal.vehicle_plans})
            except (ValueError, KeyError) as exc:
                self._issues.append(str(exc))
                continue
            signature = plan_outcome_signature(world, proposal)
            if signature in signatures:
                signatures[signature]["equivalent_policies"].append(
                    "baseline" if proposal.policy == "b0" else proposal.profile
                )
                continue
            view = {
                **proposal.model_dump(mode="json"), **self.agent.describe_plan(world, proposal),
                "routes": routes, "equivalent_policies": [],
            }
            signatures[signature] = view
            self._proposals.append(view)
            self._proposal_models[proposal.proposal_id] = proposal
        self._record(kind="proposals_computed", message=f"{len(self._proposals)} distinct feasible schedule(s)")
        return self.publish()

    def approve(self, proposal_id: str, expected_revision: int, key: str, reviewer: str) -> dict:
        world = self.require_world()
        if key in self._approvals:
            original_id, response = self._approvals[key]
            if original_id != proposal_id:
                raise CommandError("This idempotency key already belongs to another proposal.")
            return response
        proposal = self._proposal_models.get(proposal_id)
        if proposal is None:
            raise CommandError("Proposal is unavailable in this run. Regenerate choices.", 404)
        if expected_revision != world.plan_revision or proposal.based_on_plan_revision != world.plan_revision:
            raise CommandError("The plan version changed. Regenerate choices before approving.")
        try:
            self.controller.preview_routes(world, {p.vehicle_id: p.stops for p in proposal.vehicle_plans})
        except (ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc

        next_world = world.model_copy(deep=True)
        candidate = proposal.model_copy(deep=True)
        result = DefaultApprovalService(notifier=MockDispatchNotificationAdapter()).approve(
            next_world, candidate, reviewer, key
        )
        if not result.committed:
            raise CommandError(result.rejection_reason)
        for message in result.dispatch_messages:
            message.plan_version = result.new_plan_revision

        self.controller.checkpoint()
        try:
            self.controller.apply_committed_plan(next_world, [vp.vehicle_id for vp in proposal.vehicle_plans])
            self.controller.step(next_world)
        except Exception as exc:
            self.playing = False
            try:
                self.controller.restore_checkpoint(world)
            except Exception:
                self.error = "Route execution failed and SUMO could not restore its checkpoint. Reset the run."
                log.exception("SUMO checkpoint recovery failed")
            self._record(kind="execution_failed", message=str(exc))
            self.publish()
            raise CommandError(f"Route execution failed. No approval was recorded. {exc}") from exc

        if self._before is None:
            self._before = self._schedule()
        self.world = next_world
        self._policy = candidate.policy
        self._dispatch_times.update({message.task_id: world.sim_time_s for message in result.dispatch_messages})
        self._record(kind="plan_approved", message=f"{candidate.profile} approved · r{result.new_plan_revision}")
        self._proposals = []
        self._issues = []
        self.demo_pause = False
        self.publish()
        response = result.model_dump(mode="json")
        self._approvals[key] = (proposal_id, response)
        return response

    def _schedule(self) -> dict:
        return {
            vehicle.id: [stop.model_dump(mode="json") for stop in vehicle.route_stops]
            for vehicle in self.world.vehicles.values()
        }

    def _record(self, **event) -> None:
        record = {"sim_time_s": self.world.sim_time_s, **event}
        self._events.append(record)
        self._directory.mkdir(parents=True, exist_ok=True)
        with (self._directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _metrics(self, telemetry: dict) -> dict:
        comparison = self._comparisons.setdefault((self.network, self.scenario), {})
        if self._policy and self._responses:
            values = sorted(self._responses.values())
            comparison[self._policy] = {
                "n": len(values), "average_s": mean(values),
                "p90_s": values[math.ceil(len(values) * 0.9) - 1], "run_id": self.run_id,
            }
        return {
            "civilian_active": telemetry.get("civilian_active", 0),
            "civilian_observed": telemetry.get("civilian_observed", 0),
            "civilian_time_loss_s": telemetry.get("civilian_time_loss_s", 0),
            "responses_s": self._responses.copy(),
            "deadline_delays_s": self._delays.copy(),
            "comparison": comparison.copy(),
        }

    def publish(self) -> dict:
        self.sequence += 1
        telemetry = {}
        if self.controller is not None and self.world is not None and not self.error:
            telemetry = self.controller.live_telemetry(self.world)
            for entry in self.controller.event_log[self._event_cursor:]:
                self._record(**entry)
                task_id = entry.get("task_id")
                if entry["kind"] == "stop_begin" and task_id in self.world.tasks:
                    task = self.world.tasks[task_id]
                    observed = entry["sim_time_s"]
                    pickup = entry["stop_kind"] == "pickup"
                    if pickup and task_id in self._dispatch_times:
                        self._responses.setdefault(task_id, observed - self._dispatch_times[task_id])
                    if pickup and task.latest_pickup_s is not None and observed > task.latest_pickup_s:
                        self._delays[f"{task_id} pickup"] = observed - task.latest_pickup_s
                if entry["kind"] == "stop_completed" and entry["stop_kind"] == "dropoff" and task_id in self.world.tasks:
                    deadline = self.world.tasks[task_id].latest_delivery_s
                    if deadline is not None and entry["sim_time_s"] > deadline:
                        self._delays[f"{task_id} handover"] = entry["sim_time_s"] - deadline
            self._event_cursor = len(self.controller.event_log)
        self.state = {
            "sequence": self.sequence, "run_id": self.run_id,
            "simulator": "unavailable" if self.error else "running" if self.playing else "paused",
            "demo_pause": self.demo_pause, "error": self.error, "agent_mode": self.agent.label,
            "network": self.network, "scenario": self.scenario, "speed": self.speed,
            "script_applied": self._script_applied,
            "world": self.world.model_dump(mode="json") if self.world is not None else None,
            "telemetry": telemetry, "proposals": self._proposals, "proposal_issues": self._issues,
            "before": self._before, "activity": self._events[-100:], "metrics": self._metrics(telemetry),
        }
        return self.state
