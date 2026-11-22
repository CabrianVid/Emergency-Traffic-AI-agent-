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
from rescueflow.decision.agent import DecisionAgent
from rescueflow.decision.agent import fallback_key
from rescueflow.decision.alternatives import DASHBOARD, current_arrivals, generate_alternatives
from rescueflow.dashboard.network import CALL_SPOTS, nearest_node, network_geometry, prepare_network
from rescueflow.dispatch.notification import MockDispatchNotificationAdapter
from rescueflow.events.apply import apply_event
from rescueflow.events.types import EventDraft, ValidationError
from rescueflow.models.core import EventType, TaskKind, TaskPriority, TaskStatus, VehicleStatus
from rescueflow.planning.queue_dispatch import FreeUnitTrigger, queued_behind_work, waiting_calls
from rescueflow.scenario.loader import DefaultScenarioLoader, build_initial_world
from rescueflow.sumo.controller import TraciSimulationController

REVIEW_AFTER_S = 30.0
Network = Literal["ljubljana", "grid"]
Scenario = Literal["breakdown", "urgent"]
SCRIPTS = {"breakdown": "R2 breakdown · K1 recovery", "urgent": "New urgent patient"}
EventKind = Literal["breakdown", "new_emergency", "priority_change", "destination_change",
                    "road_closure", "vehicle_delay", "equipment_request"]
log = logging.getLogger(__name__)


class CommandError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class DispatchRun:
    """All methods that touch the run execute on a single worker thread."""

    def __init__(self, root: Path, agent: DispatcherAgent, decision: DecisionAgent | None = None) -> None:
        self.root = root
        self.agent = agent
        self.decision = decision or DecisionAgent(enabled=False)
        self.controller = None
        self.world = None
        self.geometry = None
        self.network: Network = "grid"
        self.scenario: Scenario = "breakdown"
        self.speed = 1
        self.playing = False
        self.error = None
        self._queued: set[str] = set()
        self._trigger = FreeUnitTrigger()
        self.run_id = ""
        self.sequence = 0
        self.state: dict = {"sequence": 0, "simulator": "starting", "agent_mode": agent.label,
                            "decision_mode": self.decision.label}
        self._comparisons: dict[tuple, dict] = {}

    def load(self, network: Network, scenario: Scenario) -> dict:
        self.close()
        self.world = None
        self.network, self.scenario = network, scenario
        self.run_id = uuid4().hex
        self.speed = 1
        self._queued, self._trigger = set(), FreeUnitTrigger()
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
        self._recommendation = None
        self._options = {}
        self._event_seq = 0
        self._notes: dict[str, str] = {}
        self._must_reroute: set[str] = set()
        self._eta_before: dict[str, dict] = {}
        self._signals: dict[str, str] = {}  # unit -> job it may have green lights for
        self._checked_at: dict[str, float] = {}
        self._reviewed: set[str] = set()
        self._directory = self.root / "state" / "dashboard" / self.run_id
        try:
            cfg, self.travel, self.nodes, self.call_nodes = prepare_network(self.root, network)
            world = build_initial_world(
                self.travel, node_mapping=self.nodes
            )
            feasibility = DefaultScenarioLoader().validate(world)
            if not feasibility.feasible:
                raise ValueError("Invalid initial schedule: " + ", ".join(i.message for i in feasibility.issues))
            self.controller = TraciSimulationController(
                str(cfg), self.travel.net_path, travel=self.travel, state_path=str(self._directory / "checkpoint.xml.gz"), allow_teleport=False,
            )
            # Commit plans while a unit crosses a junction; its route is
            # installed on the next road edge.
            self.controller.defer_junction_reroutes = True
            self.controller.detour_civilians = True
            self.controller.finish_service_first = self.controller.recover_stuck_units = True
            self.controller.green_wave.enabled = False
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
            self._apply_signals(world)
            if not self._proposals and self._trigger.fires(world, lambda task: task.id in self._queued):
                self._record(kind="unit_available", message="A unit became free for a waiting call: options recalculated")
                return self.propose(freed=True)
            if not self._proposals and self._review(world):
                return self.propose()
            return self.publish()
        except Exception as exc:
            self.playing = False
            self.error = f"SUMO execution paused: {exc}"
            log.exception("SUMO step failed")
            self.publish()
            raise CommandError(self.error, 503) from exc

    def _unreached(self, world) -> list:
        """Priority 1 patients nobody has reached yet."""
        standing = {
            s.task_id for v in self.controller.serving(world)
            for s in world.vehicles[v].route_stops if s.service_completed_at_s is None}
        return [t for t in world.tasks.values()
                if t.priority == TaskPriority.critical and t.id not in standing
                and t.status in (TaskStatus.pending, TaskStatus.unserved, TaskStatus.planned)]

    def _review(self, world) -> bool:
        """Every 30 s, look again at each unreached priority 1 patient.

        The first look always asks (if there is a choice). Later looks ask only
        when the patient has no unit, is running late, or a clearly better plan
        exists; otherwise the check is logged."""
        now = world.sim_time_s
        waiting = self._unreached(world)
        for task in waiting:
            self._checked_at.setdefault(task.id, now)
        due = [t for t in waiting if now - self._checked_at[t.id] >= REVIEW_AFTER_S]
        if not due:
            return False
        self.controller.use_live_traffic(self.travel)
        options, _ = generate_alternatives(world, self.travel, None, strategies=DASHBOARD, limit=4,
                                           serving=self.controller.serving(world))
        for task in due:
            self._checked_at[task.id] = now
        keep = next((o for o in options if o.keeps), None)
        others = [o for o in options if not o.keeps]
        ids = [t.id for t in due]
        if others:
            first = [t for t in ids if t not in self._reviewed]
            troubled = keep is None or any(
                keep.arrivals.get(t.id, {}).get("unit") is None
                or (t.latest_pickup_s is not None and now + keep.arrivals[t.id]["eta_s"] > t.latest_pickup_s) for t in due)
            best = min(others, key=fallback_key)
            better = keep is not None and fallback_key(best)[:2] <= fallback_key(keep)[:2] and (
                (keep.metrics["mean_call_response_s"] or 0) - (best.metrics["mean_call_response_s"] or 0) >= 15)
            if first or troubled or better:
                self._reviewed.update(ids)
                self._record(kind="review", message=f"{', '.join(ids)} not reached after {REVIEW_AFTER_S:.0f} s", impacted_tasks=ids)
                return True
        arrivals = keep.arrivals if keep else {}
        text = ", ".join(f"{t}: {arrivals[t]['unit'] or 'first free unit'} in {arrivals[t]['eta_s']:.0f} s" for t in ids if t in arrivals)
        self._record(kind="review_ok", message=text or ", ".join(ids), impacted_tasks=ids)
        return False

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

    def inject(self, trigger: str, ref: str | None = None, duration_s: float | None = None,
               kind: str | None = None) -> dict:
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
        elif trigger == "event":
            drafts = [self._structured_draft(kind, ref, now)]
        else:
            if self._script_applied:
                raise CommandError("Reset the run to replay its scripted incident.")
            drafts = self._scripted_drafts(now)
        state = self._apply_drafts(drafts)
        if trigger == "report":
            self._reports.pop(ref, None)
        elif trigger == "scenario":
            self._script_applied = True
            return self.publish()
        return state

    def _apply_drafts(self, drafts: list[EventDraft]) -> dict:
        """Validate every draft, apply them all, then compute options."""
        world = self.require_world()
        validated = []
        for draft in drafts:
            if draft.type == EventType.vehicle_breakdown and draft.vehicle_id in world.vehicles:
                # SUMO cannot freeze a unit inside a junction: let it clear first.
                for _ in range(30):
                    if not (world.vehicles[draft.vehicle_id].sumo_edge_id or "").startswith(":"):
                        break
                    self.controller.step(world)
                draft = draft.model_copy(update={"sim_time_s": world.sim_time_s})
            event = self.agent.validate_event(draft, world)
            if isinstance(event, ValidationError):
                raise CommandError(event.message, 422)
            validated.append(event)
            if draft.type == EventType.vehicle_breakdown:
                edge = world.vehicles[draft.vehicle_id].sumo_edge_id
                if edge and edge.startswith(":"):
                    raise CommandError(f"{draft.vehicle_id} is in a junction. Advance the simulation and retry.")
        self._before = self._schedule()
        self.controller.use_live_traffic(self.travel)
        before = current_arrivals(world, self.travel, self.controller.serving(world))
        self.playing = False
        self.demo_pause = True
        affected: list[str] = []
        for event in validated:
            impacted = self.agent.get_impacted_tasks(event, world)
            payload = event.event.payload
            if event.event.type == EventType.vehicle_breakdown:
                self.controller.freeze_vehicle_and_locate(world, payload["vehicle_id"])
                # With a patient aboard, offer to send a second unit for the transfer.
                payload["patient_transfer"] = True
            elif event.event.type == EventType.road_closure:
                fleet = self.controller.live_telemetry(world)["fleet"]
                affected = [v for v, seen in fleet.items() if payload["edge_id"] in seen["route"][1:]
                            and world.vehicles[v].status != VehicleStatus.broken]
                self._must_reroute.update(affected)
                self.controller.apply_road_closure_physically(world, payload["edge_id"])
            apply_event(world, event)
            if event.event.type == EventType.vehicle_delay:
                unit = world.vehicles[payload["vehicle_id"]]
                unit.available_after_s = max(unit.available_after_s, world.sim_time_s + float(payload["delay_s"]))
                self.controller.hold_vehicle(world, payload["vehicle_id"], float(payload["delay_s"]))
            if event.event.type == EventType.destination_changed:
                # A changed destination is an instruction, not a dispatch
                # choice: the unit already carrying the task drives there.
                units = [v.id for v in world.vehicles.values()
                         if any(s.task_id == payload["task_id"] and s.service_completed_at_s is None
                                for s in v.route_stops)]
                self.controller.apply_committed_plan(world, units)
            self._record(kind=event.event.type.value, message=event.event.impact_summary,
                         impacted_tasks=[task.task_id for task in impacted],
                         **({"units": affected} if event.event.type == EventType.road_closure else {}))
        if validated and all(e.event.type == EventType.road_closure for e in validated) and not affected:
            self._record(kind="closure_clear", message="No unit's path uses the closed road")
            self.demo_pause = False
            return self.publish()
        self._eta_before = before
        return self.propose()

    def _call_draft(self, pickup: str, priority: str, needs_als: bool, note: str = "") -> EventDraft:
        """One emergency call. Target times to the patient are synthetic."""
        now = self.world.sim_time_s
        hospital = next((h for h in self.world.receiving_sites.values()
                         if TaskKind.urgent_response in h.accepted_task_kinds and h.node_id != pickup), None)
        if hospital is None:
            raise CommandError("No receiving hospital is available for this location.", 422)
        target = {"critical": 480, "high": 900, "routine": 1800}[priority]
        payload = {
            "task_id": f"CALL-{self._event_seq}", "pickup_node": pickup, "dropoff_node": hospital.node_id,
            "priority": priority, "latest_pickup_s": now + target, "latest_delivery_s": now + target + 1200,
            "required_capabilities": ["ALS"] if needs_als else [],
        }
        if note:
            payload["note"] = note
            self._notes[payload["task_id"]] = note
        return EventDraft(type=EventType.new_urgent_task, sim_time_s=now, payload=payload)

    def log_call(self, x: float, y: float, priority: str, needs_als: bool, note: str) -> dict:
        """The dispatcher logs an emergency; it starts at the nearest reachable junction."""
        self.require_world()
        left, bottom, right, top = self.geometry["bounds"]
        if not (left - 200 <= x <= right + 200 and bottom - 200 <= y <= top + 200):
            raise CommandError("That location is outside the served area.", 422)
        self._event_seq += 1
        pickup = nearest_node(self.travel, self.call_nodes, x, y)
        return self._apply_drafts([self._call_draft(pickup, priority, needs_als, note)])

    def _structured_draft(self, kind: str | None, ref: str | None, now: float) -> EventDraft:
        """Dashboard event buttons. `ref` is the selected unit or task."""
        world = self.world
        hospitals = list(world.receiving_sites.values())
        self._event_seq += 1
        if kind == "breakdown":
            unit = world.vehicles.get(ref or "")
            if unit is None:
                raise CommandError("Select an ambulance to report a breakdown.", 422)
            return EventDraft(type=EventType.vehicle_breakdown, sim_time_s=now, vehicle_id=unit.id,
                              equipment_ids_reported_stranded=list(unit.equipment_aboard))
        if kind == "vehicle_delay":
            if ref not in world.vehicles:
                raise CommandError("Select an ambulance to report a delay.", 422)
            return EventDraft(type=EventType.vehicle_delay, sim_time_s=now, vehicle_id=ref, payload={"delay_s": 120})
        if kind in ("priority_change", "destination_change"):
            task = world.tasks.get(ref or "")
            if task is None:
                raise CommandError("Select an active task first.", 422)
            if kind == "priority_change":
                new = "critical" if task.priority != TaskPriority.critical else "high"
                return EventDraft(type=EventType.priority_changed, sim_time_s=now, task_id=task.id,
                                  payload={"new_priority": new, "reason": "crew reassessment"})
            other = next((h.node_id for h in hospitals if h.node_id != task.dropoff_node), None)
            if other is None:
                raise CommandError("No alternative hospital is available.", 422)
            return EventDraft(type=EventType.destination_changed, sim_time_s=now, task_id=task.id,
                              payload={"new_dropoff_node": other, "reason": "receiving hospital changed"})
        if kind == "new_emergency":
            spots = CALL_SPOTS[self.network]
            pickup = self.nodes[spots[(self._event_seq - 1) % len(spots)]]
            return self._call_draft(pickup, "critical", False)
        if kind == "equipment_request":
            free = [a for a in world.assets.values() if a.location.kind != "vehicle"
                    and not any(a.id in t.required_asset_ids and t.status not in
                                (TaskStatus.completed, TaskStatus.interrupted, TaskStatus.unserved)
                                for t in world.tasks.values())]
            if not free:
                raise CommandError("No idle equipment is available to request.", 422)
            asset = free[0]
            target = next((h.node_id for h in hospitals if h.node_id != asset.location.ref_id), hospitals[0].node_id)
            return EventDraft(type=EventType.equipment_request, sim_time_s=now, payload={
                "task_id": f"EQ-{self._event_seq}", "asset_id": asset.id, "dropoff_node": target,
                "priority": "high", "latest_delivery_s": now + 1200,
            })
        raise CommandError(f"Unsupported event {kind!r}.", 422)

    def _scripted_drafts(self, now: float) -> list[EventDraft]:
        if self.scenario == "breakdown":
            return [EventDraft(type=EventType.vehicle_breakdown, sim_time_s=now, vehicle_id="R2",
                               equipment_ids_reported_stranded=["K1"])]
        pickup = self.nodes["E2"]
        return [EventDraft(type=EventType.new_urgent_task, sim_time_s=now, payload={
            "task_id": "URGENT", "pickup_node": pickup,
            "dropoff_node": self.world.receiving_sites["H2"].node_id,
            "priority": "critical", "latest_pickup_s": now + 240, "latest_delivery_s": now + 950,
        })]

    def propose(self, freed: bool = False) -> dict:
        """Recalculate feasible alternatives and ask the decision agent to rank them."""
        world = self.require_world()
        self.playing = False
        self.demo_pause = True
        event = world.events[-1] if world.events and not freed else None
        # Plan with the travel times SUMO observes now, as the benchmark does.
        self.controller.use_live_traffic(self.travel)

        def route_check(current, proposal):
            self.controller.preview_routes(current, {p.vehicle_id: p.stops for p in proposal.vehicle_plans})

        options, notes = generate_alternatives(world, self.travel, event, route_check=route_check, strategies=DASHBOARD,
                                               limit=4, serving=self.controller.serving(world))
        recommendation = self.decision.rank(world, event, options)
        rank = {option_id: index for index, option_id in enumerate(recommendation.ranking)}
        options.sort(key=lambda o: rank.get(o.option_id, 99))
        self._issues = notes
        self._proposals = []
        self._proposal_models = {}
        self._options = {}
        for option in options:
            proposal = option.proposal
            # Units on a closed road need a new path even when their jobs do not change.
            stops = {v: world.vehicles[v].route_stops for v in self._reroutes(world)}
            routes = self.controller.preview_routes(world, {**stops, **{p.vehicle_id: p.stops for p in proposal.vehicle_plans}})
            self._proposals.append({
                **proposal.model_dump(mode="json"), **self.agent.describe_plan(world, proposal),
                "routes": routes, "equivalent_policies": [],
                "option_id": option.option_id, "strategy": option.strategy, "description": option.description,
                "rank": rank.get(option.option_id, 99) + 1,
                "recommended": option.option_id == recommendation.recommended,
                "ai_probability": recommendation.probabilities.get(option.option_id),
                "metrics": option.metrics, "signal_priority_units": option.signal_priority_units,
                "warnings": option.warnings, "arrivals": option.arrivals, "keeps": option.keeps,
                "reroutes": sorted(stops), "lights": self._lights(world, option, routes),
            })
            self._proposal_models[proposal.proposal_id] = proposal
            self._options[proposal.proposal_id] = option
        self._recommendation = {**recommendation.as_dict(), "at_revision": world.plan_revision}
        source = {"openai-decisions": "AI ranked", "single-feasible-option": "single option",
                  "deterministic-fallback": "fallback ranked"}[recommendation.source]
        self._record(kind="proposals_computed",
                     message=f"{len(options)} feasible option(s), {source}"
                     + (f" · recommended {recommendation.recommended}" if recommendation.recommended else ""))
        return self.publish()

    def _reroutes(self, world) -> list[str]:
        return sorted(v for v in self._must_reroute if world.vehicles[v].status != VehicleStatus.broken
                      and any(s.service_completed_at_s is None for s in world.vehicles[v].route_stops))

    def _lights(self, world, option, routes: dict[str, list[str]]) -> dict[str, int]:
        """Traffic lights between each priority 1 unit and its next stop."""
        fleet = self.controller.live_telemetry(world)["fleet"]
        plans = {p.vehicle_id: p.stops for p in option.proposal.vehicle_plans}
        found = {}
        for unit in option.signal_priority_units:
            stop = next((s for s in plans.get(unit, world.vehicles[unit].route_stops) if s.service_completed_at_s is None), None)
            if stop is not None and self._signals.get(unit) == stop.task_id:
                continue  # already allowed for this job
            lights = set()
            for edge in routes.get(unit) or fleet.get(unit, {}).get("route", ()):
                if edge.startswith(":"):
                    continue
                node = self.travel.net.getEdge(edge).getToNode()
                if stop is not None and node.getID() == stop.node_id:
                    break
                if node.getType().startswith("traffic_light"):
                    lights.add(node.getID())
            found[unit] = len(lights)
        return found

    @staticmethod
    def _next_job(world, unit: str) -> str | None:
        return next((s.task_id for s in world.vehicles[unit].route_stops if s.service_completed_at_s is None), None)

    def _apply_signals(self, world) -> None:
        """A permission covers one unit and one job, and ends with it."""
        self._signals = {unit: job for unit, job in self._signals.items()
                         if job and self._next_job(world, unit) == job and world.vehicles[unit].status != VehicleStatus.broken}
        self.controller.signal_units = set(self._signals)
        self.controller.green_wave.enabled = bool(self._signals)

    def _decided(self, world) -> None:
        """The dispatcher has just decided: urgent calls are looked at again in 30 s."""
        self._must_reroute = set()
        self._eta_before = {}
        self._checked_at = {t.id: world.sim_time_s for t in self._unreached(world)}

    def reject(self) -> dict:
        world = self.require_world()
        if not self._proposals:
            raise CommandError("There are no proposals to reject.")
        self._record(kind="proposals_rejected", message=f"Dispatcher rejected {len(self._proposals)} option(s)")
        self._proposals, self._proposal_models, self._options, self._issues = [], {}, {}, []
        self._recommendation = None
        self.demo_pause = False
        if self._reroutes(world):
            # Changing no job still means driving around the closed road.
            self.controller.apply_committed_plan(world, self._reroutes(world))
        self._decided(world)
        return self.publish()

    def approve(self, proposal_id: str, expected_revision: int, key: str, reviewer: str,
                signal_priority: bool = False) -> dict:
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
            touched = {vp.vehicle_id for vp in proposal.vehicle_plans}
            self.controller.apply_committed_plan(next_world, sorted(touched | set(self._reroutes(next_world))))
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
        # Calls this decision leaves waiting are offered again when a unit frees up.
        self._queued = {t.id for t in [*waiting_calls(next_world), *queued_behind_work(next_world)]}
        self._trigger.mark(next_world)
        self._policy = candidate.policy
        option = self._options.get(proposal_id)
        authorised = bool(signal_priority and option and option.signal_priority_units)
        if option and option.signal_priority_units:
            # "No" applies to the units just asked about; earlier permissions stay.
            if authorised:
                self._signals.update({unit: self._next_job(next_world, unit) for unit in option.signal_priority_units})
        self._apply_signals(next_world)
        if authorised:
            self._record(kind="signal_priority_authorised",
                         message=f"Green-wave priority authorised for {', '.join(option.signal_priority_units)}")
        self._dispatch_times.update({message.task_id: world.sim_time_s for message in result.dispatch_messages})
        label = f"option {option.option_id} ({option.strategy})" if option else candidate.profile
        followed = self._recommendation and option and self._recommendation.get("recommended") == option.option_id
        self._record(kind="plan_approved", message=f"Dispatcher approved {label}"
                     f"{' · as recommended' if followed else ' · overriding the recommendation'} · r{result.new_plan_revision}")
        self._proposals = []
        self._issues = []
        self._recommendation = None
        self.demo_pause = False
        self._decided(next_world)
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

    def _recent_activity(self) -> list[dict]:
        """Recent events. Frequent signal events must not push decisions out of the shift log."""
        signals = sum("tls_id" in event for event in self._events)
        others = len(self._events) - signals
        kept = []
        for event in self._events:
            if "tls_id" in event:
                if signals <= 60:
                    kept.append(event)
                signals -= 1
            else:
                if others <= 200:
                    kept.append(event)
                others -= 1
        return kept

    def publish(self) -> dict:
        self.sequence += 1
        telemetry = {}
        if self.controller is not None and self.world is not None and not self.error:
            telemetry = self.controller.live_telemetry(self.world)
            telemetry["serving"] = sorted(self.controller.serving(self.world))
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
            "notes": self._notes, "eta_before": self._eta_before, "signal_units": sorted(self._signals),
            "review_at": {} if self.world is None or self.controller is None or self.error else {
                t.id: self._checked_at[t.id] + REVIEW_AFTER_S for t in self._unreached(self.world) if t.id in self._checked_at}, "recommendation": self._recommendation, "decision_mode": self.decision.label,
            "signal_priority_active": bool(self.controller and self.controller.green_wave.enabled),
            "before": self._before, "activity": self._recent_activity(), "metrics": self._metrics(telemetry),
        }
        return self.state
