"""Execute one policy in one fresh SUMO process and write observed outcomes."""

from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean, median

from rescueflow.approval.service import DefaultApprovalService
from rescueflow.benchmark.inputs import digest, write_json
from rescueflow.dispatch.notification import MockDispatchNotificationAdapter
from rescueflow.events.apply import apply_event
from rescueflow.events.interpreter import RuleBasedEventInterpreter
from rescueflow.events.types import EventDraft, ValidationError
from rescueflow.models.core import TaskStatus, WorldState
from rescueflow.planning.baseline import BaselinePlanner
from rescueflow.planning.optimized import OptimizedPlanner
from rescueflow.sumo.controller import TraciSimulationController
from rescueflow.sumo.env import ensure_sumolib_importable
from rescueflow.travel import SumoTravelTimeProvider


def percentile90(values):
    ordered = sorted(values)
    return ordered[math.ceil(0.9 * len(ordered)) - 1] if ordered else None


def statistics(values: list[float]) -> dict:
    return {
        "n": len(values), "mean_s": mean(values) if values else None,
        "median_s": median(values) if values else None, "p90_s": percentile90(values),
    }


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(dict.fromkeys(key for row in rows for key in row)))
        writer.writeheader()
        writer.writerows(rows)


def _trip_rows(path: Path) -> list[dict]:
    tree = ET.parse(path)
    rows = []
    for trip in tree.getroot().iterfind("tripinfo"):
        read = trip.attrib.__getitem__
        rows.append({
            "vehicle_id": read("id"), "type": read("vType"), "depart_s": float(read("depart")),
            "arrival_s": float(read("arrival")), "duration_s": float(read("duration")),
            "waiting_s": float(read("waitingTime")), "time_loss_s": float(read("timeLoss")),
            "distance_m": float(read("routeLength")), "vaporized": trip.get("vaporized", ""),
            "devices": trip.get("devices", ""),
        })
    return rows


def audit_stop_warnings(lines: list[str], trips: list[dict]) -> tuple[int, int]:
    """Distinguish a lost stop from SUMO closing an intentionally replaced trip."""
    replaced = {(row["vehicle_id"], row["arrival_s"]) for row in trips if row["vaporized"] == "traci"}
    restarted = {(row["vehicle_id"], row["depart_s"]) for row in trips}
    superseded = unmatched = 0
    for line in lines:
        match = re.search(r"Vehicle '([^']+)' skips stop on lane .* time=(\d+(?:\.\d+)?)", line)
        if match is None:
            continue
        marker = match[1], float(match[2])
        if marker in replaced and marker in restarted:
            superseded += 1
        else:
            unmatched += 1
    return superseded, unmatched


class LogisticsRun:
    def __init__(self, manifest: dict, controller, world, travel, policy: str, output: Path, record: bool):
        self.inputs, self.controller, self.world, self.travel = manifest, controller, world, travel
        self.policy, self.output, self.record = policy, output, record
        self.events: list[dict] = []
        self.frames: list[dict] = []
        self.changed_assignments = 0
        self.solve_ms: list[float] = []
        self.failed: list[str] = []
        self.report_times: dict[str, float] = {}
        self.planned: dict[str, dict] = {}
        self.at_risk: set[str] = set()

    def log(self, kind: str, **fields):
        self.events.append({"kind": kind, "sim_time_s": self.world.sim_time_s, **fields})

    def apply_incident(self, incident: dict) -> list[str]:
        current = self.world
        draft = EventDraft(
            type=incident["type"], sim_time_s=current.sim_time_s,
            vehicle_id=incident.get("vehicle_id"), equipment_ids_reported_stranded=incident.get("assets", []),
            payload=incident.get("payload", {}),
        )
        validated = RuleBasedEventInterpreter().validate_event(draft, current)
        if isinstance(validated, ValidationError):
            raise ValueError(f"{validated.code}: {validated.message}")
        affected = []
        if draft.type.value == "vehicle_breakdown":
            vehicle = current.vehicles[draft.vehicle_id]
            if vehicle.sumo_edge_id is None or vehicle.sumo_edge_id.startswith(":"):
                raise ValueError(f"Frozen incident requires R2 on a road; got {vehicle.sumo_edge_id!r}")
            if any(task.patient_slots and task.assigned_vehicle_id == vehicle.id and task.status == TaskStatus.en_route_dropoff
                   for task in current.tasks.values()):
                raise ValueError("Breakdown fixture has a patient aboard R2")
            self.controller.freeze_vehicle_and_locate(current, vehicle.id)
        if draft.type.value == "road_closure":
            edge = draft.payload["edge_id"]
            affected = [
                vid for vid, obs in self.controller.live_telemetry(current)["fleet"].items()
                if edge in obs["route"][1:]
            ]
            self.controller.apply_road_closure_physically(current, edge)
            if not affected:
                raise ValueError(f"Closure {edge} did not invalidate any remaining committed route")
        apply_event(current, validated)
        if draft.type.value == "new_urgent_task":
            self.report_times[draft.payload["task_id"]] = current.sim_time_s
        self.log("incident_validated", incident=incident, impact=validated.event.impact_summary,
                 affected_routes=affected, assets={key: value.location.model_dump() for key, value in current.assets.items()})
        self.at_risk.update(t.id for t in current.tasks.values() if t.status != TaskStatus.completed)
        return affected

    def repair(self, affected: list[str]):
        started = time.perf_counter()
        current = self.world
        # Plan on a detached copy with the current road endpoint for an
        # ambulance whose route can be replaced. Locked stops retain custody.
        planning = current.model_copy(deep=True)
        self.travel.set_closed_edges(current.closed_edges)
        for vehicle in planning.vehicles.values():
            edge = vehicle.sumo_edge_id
            if edge and not edge.startswith(":"):
                vehicle.node_id = self.travel.net.getEdge(edge).getToNode().getID()
        if self.policy in ("B0", "A2"):
            plan = BaselinePlanner(self.travel, solve_budget_s=15, nearest_available_emergency=True).baseline_repair(
                planning, current.events[-1],
            )
        else:
            plan = OptimizedPlanner(self.travel, solve_budget_s=15).optimized_repair(
                planning, current.events[-1], ["urgent_first"],
            )[0]
        self.solve_ms.append((time.perf_counter() - started) * 1000)
        self.log("proposal_computed", revision=plan.based_on_plan_revision,
                 estimated_score=plan.score.model_dump(), unserved=plan.unserved_task_ids,
                 issues=[issue.code for issue in plan.feasibility.issues])
        if not plan.feasibility.feasible:
            self.failed.extend(f"plan: {issue.code}: {issue.message}" for issue in plan.feasibility.issues)
            return

        touched = sorted({vp.vehicle_id for vp in plan.vehicle_plans} | set(affected))
        before = {key: task.assigned_vehicle_id for key, task in current.tasks.items()}
        candidate = current.model_copy(deep=True)
        result = DefaultApprovalService(notifier=MockDispatchNotificationAdapter()).approve(
            candidate, plan, approved_by="simulated-operator", idempotency_key=str(len(self.solve_ms))
        )
        if not result.committed:
            self.failed.append(f"approval: {result.rejection_reason}")
            return
        try:
            self.controller.preview_routes(candidate, {vid: candidate.vehicles[vid].route_stops for vid in touched})
            self.controller.apply_committed_plan(candidate, touched)
        except (RuntimeError, ValueError, KeyError) as exc:
            self.failed.append(f"route installation: {exc}")
            raise
        self.world = candidate
        self.controller.green_wave.enabled = self.policy in ("A2", "A3")
        for key, task in candidate.tasks.items():
            if before.get(key) and task.assigned_vehicle_id != before[key]:
                self.changed_assignments += 1
        for vp in plan.vehicle_plans:
            for stop in vp.stops:
                if stop.task_id and stop.service_completed_at_s is None:
                    prediction = self.planned.setdefault(stop.task_id, {})
                    prediction[f"planned_{stop.kind.value}_s"] = stop.arrival_s
                    prediction["planned_vehicle"] = vp.vehicle_id
        self.log("plan_approved", policy=self.policy, revision=result.new_plan_revision, vehicles=touched,
                 dispatch=[message.model_dump(mode="json") for message in result.dispatch_messages])

    def execute(self):
        self.controller.green_wave.enabled = False
        self.controller.load_scenario(self.world, keep_connected=True)
        pending = list(self.inputs["incidents"])
        scheduled = []
        horizon = self.inputs["horizon_s"]
        delay = self.inputs["approval_delay_s"] + self.inputs["mobilization_delay_s"]
        self.at_risk.update(self.world.tasks)
        while self.world.sim_time_s < horizon:
            now = self.world.sim_time_s
            for incident in pending[:]:
                ready = incident.get("time_s") == now
                if "after_task" in incident:
                    task = self.world.tasks.get(incident["after_task"])
                    asset = self.world.assets["K1"]
                    ready = bool(task and task.status == TaskStatus.completed and asset.location.kind == "hospital"
                                 and asset.location.ref_id == "A6")
                if ready:
                    affected = self.apply_incident(incident)
                    scheduled.append((now + delay, affected))
                    pending.remove(incident)
            if scheduled and now >= scheduled[0][0]:
                # Allow a short, recorded junction clearance before route
                # installation. The base decision/mobilization delay is constant.
                if not any((v.sumo_edge_id or "").startswith(":") for v in self.world.vehicles.values()) or now >= scheduled[0][0] + 8:
                    due, affected = scheduled.pop(0)
                    self.log("dispatch_release", approval_delay_s=self.inputs["approval_delay_s"],
                             mobilization_delay_s=self.inputs["mobilization_delay_s"], junction_delay_s=now - due)
                    self.repair(affected)
            if self.record and now % 20 == 0:
                live = self.controller.live_telemetry(self.world)
                self.frames.append({
                    "time_s": now, "fleet": live["fleet"],
                    "kit": self.world.assets["K1"].location.model_dump(),
                    "completed": sum(t.status == TaskStatus.completed for t in self.world.tasks.values()),
                })
            self.controller.step(self.world, capture_positions=False)
        for incident in pending:
            if "after_task" in incident:
                self.at_risk.add(incident["payload"]["task_id"])
                self.log("dependency_unreleased", task_id=incident["payload"]["task_id"], prerequisite=incident["after_task"])

    def task_results(self) -> list[dict]:
        first: dict[tuple, dict] = {}
        final: dict[tuple, dict] = {}
        events = self.controller.event_log
        for event in events:
            key = (event.get("task_id"), event.get("stop_kind"))
            if event["kind"] == "stop_begin":
                first.setdefault(key, event)
            elif event["kind"] == "stop_completed":
                final[key] = event
                if key not in first:
                    self.failed.append(f"completion without observed stop: {key}")
        rows = []
        for task_id in sorted(self.at_risk):
            task = self.world.tasks.get(task_id)
            if task is not None and task.superseded_by:
                continue
            priority = task.priority.value if task else "high"
            pickup = first.get((task_id, "pickup"))
            handover = final.get((task_id, "dropoff"))
            deadline = task.latest_delivery_s if task else 2400
            completion = handover["sim_time_s"] if handover else None
            late_s = max(0, completion - deadline) if completion is not None and deadline is not None else None
            report = self.report_times.get(task_id)
            response = None
            if task and report is not None and pickup and priority == "critical":
                unit = self.world.vehicles[pickup["vehicle_id"]]
                target = self.travel.net.getNode(task.pickup_node).getCoord()
                if set(task.required_capabilities) <= set(unit.capabilities) and math.dist(target, pickup["xy"]) <= 15:
                    response = pickup["sim_time_s"] - report
                else:
                    self.failed.append(f"incorrect responder or pickup position for {task_id}")
            if task and task.time_window_hard:
                if late_s or (pickup and task.latest_pickup_s is not None and pickup["sim_time_s"] > task.latest_pickup_s):
                    self.failed.append(f"hard time window violated by {task_id}")
            rows.append({
                "task_id": task_id, "priority": priority, "status": task.status.value if task else "dependency_unreleased",
                "reported_s": report, "vehicle_id": pickup["vehicle_id"] if pickup else None,
                "observed_pickup_s": pickup["sim_time_s"] if pickup else None,
                "observed_completion_s": completion, "deadline_s": deadline, "lateness_s": late_s,
                "response_s": response, "is_emergency_call": task_id.startswith("CALL-"),
                **self.planned.get(task_id, {}),
            })
        return rows

    def recovery_correct(self, rows: list[dict]) -> bool | None:
        if self.inputs["scenario"] != "breakdown":
            return None
        records = {row["task_id"]: row for row in rows}
        recovery, downstream = records["T3-RECOVERY"], records["K1-FOLLOWUP"]
        pickup = recovery["observed_pickup_s"]
        complete = recovery["observed_completion_s"]
        pickup_done = next((e["sim_time_s"] for e in self.controller.event_log if
                            e["kind"] == "stop_completed" and e["task_id"] == "T3-RECOVERY" and e["stop_kind"] == "pickup"), None)
        if complete is None:
            return False
        ordered = pickup is not None and pickup_done is not None and pickup_done - pickup >= 180 and pickup_done < complete
        downstream_time = downstream["observed_pickup_s"]
        correct = bool(ordered and recovery["vehicle_id"] != "R2"
                       and (downstream_time is None or downstream_time >= complete)
                       and self.world.assets["K1"].location.ref_id == "A6")
        if not correct:
            self.failed.append("K1 custody/recovery order violated")
        return correct


def execute(inputs: Path, output: Path, policy: str, record: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    git_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    meta = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    for filename, fingerprint in meta["files"].items():
        if digest(inputs / filename) != fingerprint:
            raise ValueError(f"Input changed: {filename}")
    checksum = digest(inputs / "manifest.json")
    world = WorldState.model_validate_json((inputs / "world.json").read_bytes())
    travel = SumoTravelTimeProvider(str(inputs / "grid.net.xml"))
    controller = TraciSimulationController(
        str(inputs / "run.sumocfg"), str(inputs / "grid.net.xml"), str(output / "unused.state.xml.gz"),
        meta["seed"], allow_teleport=False, observations_dir=output, junction_stops=True,
    )
    runner = None
    failures = []
    try:
        if meta["suite"] == "logistics":
            runner = LogisticsRun(meta, controller, world, travel, policy, output, record)
            runner.execute()
        else:
            controller.green_wave.enabled = policy == "A2"
            controller.priority_probe_ids.add(meta["mission"]["vehicle_id"])
            controller.load_scenario(world, keep_connected=True)
            controller.step(world, n=meta["horizon_s"] - 1, capture_positions=False)
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        controller.close()

    observations = controller.event_log
    dangerous = [e for e in observations if e["kind"] in ("collision", "teleport_started")]
    if dangerous:
        failures.append(f"SUMO collision/teleport observations: {len(dangerous)}")
    missing_stops = [e for e in observations if e["kind"] == "missing_sumo_stop"]
    if missing_stops:
        failures.extend(f"{e['vehicle_id']} had route stops missing from SUMO" for e in missing_stops)
    try:
        trips = _trip_rows(output / "tripinfo.xml")
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        failures.append(f"invalid SUMO tripinfo: {exc}")
        trips = []
    warnings = output / "warnings.log"
    warnings.touch(exist_ok=True)
    warning_lines = warnings.read_text(encoding="utf-8", errors="replace").splitlines()
    civilian = [trip for trip in trips if trip["type"] == "civilian" and trip["arrival_s"] >= 0 and not trip["vaporized"]]
    response = []
    rows = runner.task_results() if runner else []
    recovery = runner.recovery_correct(rows) if runner and any(r["task_id"] == "T3-RECOVERY" for r in rows) else None
    if runner:
        failures.extend(runner.failed)
        response = [row["response_s"] for row in rows if row["response_s"] is not None]
    mission = next((trip for trip in trips if trip["vehicle_id"] == "priority_probe"), None)
    if meta["suite"] == "traffic" and mission and "bluelight" not in mission["devices"]:
        failures.append("Frozen urgent probe did not receive the blue-light device")
    if any("Error:" in line for line in warning_lines):
        failures.append("SUMO reported errors; see warnings.log")
    superseded_stops, skipped_stops = audit_stop_warnings(warning_lines, trips)
    if skipped_stops:
        failures.append("SUMO skipped a scheduled vehicle stop; see warnings.log")
    requested = sum(e["kind"] == "priority_requested" for e in observations)
    restored = sum(e["kind"] == "restored" for e in observations)
    if requested != restored:
        failures.append(f"unfinished signal requests: {requested - restored}")
    by_priority = {}
    for priority in meta["weights"]:
        group = [row for row in rows if row["priority"] == priority]
        served = [row for row in group if row["observed_completion_s"] is not None]
        on_time = [
            row for row in served if row["deadline_s"] is not None and row["lateness_s"] == 0
        ]
        with_deadline = [row for row in group if row["deadline_s"] is not None]
        by_priority[priority] = {
            "total": len(group), "completed": len(served), "unserved": len(group) - len(served),
            "on_time": len(on_time), "with_deadline": len(with_deadline),
            "on_time_pct": 100 * len(on_time) / len(with_deadline) if with_deadline else None,
        }
    emergency_count = sum(row["is_emergency_call"] for row in rows)
    metrics = {
        **{key: meta[key] for key in ("suite", "scenario", "seed", "horizon_s", "response_target_s", "weights")},
        "policy": policy, "inputs_sha256": checksum, "valid": not failures, "failures": failures,
        "sumo_version": controller._read_sumo_version(ensure_sumolib_importable()),
        "git_sha": git_sha,
        "emergency_count": emergency_count, "emergencies_unserved": emergency_count - len(response),
        "responses_s": {row["task_id"]: row["response_s"] for row in rows if row["response_s"] is not None},
        "response": statistics(response),
        "within_target": sum(t <= meta["response_target_s"] for t in response),
        "within_target_pct": 100 * sum(t <= meta["response_target_s"] for t in response) / emergency_count if emergency_count else None,
        "weighted_tardiness_s": sum(meta["weights"][r["priority"]] * (r["lateness_s"] or 0) for r in rows),
        "served_lateness": statistics([r["lateness_s"] for r in rows if r["lateness_s"] is not None]),
        "lateness_samples_s": [r["lateness_s"] for r in rows if r["lateness_s"] is not None],
        "by_priority": by_priority, "recovery_correct": recovery,
        "changed_assignments": runner.changed_assignments if runner else 0,
        "fleet_distance_m": sum(t["distance_m"] for t in trips if t["type"].startswith("ambulance")),
        "solver_wall_ms": runner.solve_ms if runner else [], "llm_wall_ms": None,
        "approval_delay_s": meta["approval_delay_s"], "mobilization_delay_s": meta["mobilization_delay_s"],
        "civilian_planned": meta["civilian_departures"],
        "civilian_unfinished": meta["civilian_departures"] - len(civilian),
        "civilian_time_loss": statistics([trip["time_loss_s"] for trip in civilian]),
        "mission": mission,
        "priority_requests": requested, "priority_restored": restored,
        "preemptions": sum(e["kind"] == "phase_shortened" for e in observations),
        "seconds_shortened": sum(e.get("shortened_by_s", 0) for e in observations if e["kind"] == "phase_shortened"),
        "priority_conflicts": sum(e["kind"] == "priority_deferred" for e in observations),
        "superseded_stop_warnings": superseded_stops, "unmatched_stop_warnings": skipped_stops,
        "warnings": len(warning_lines),
    }
    world = runner.world if runner else world
    (output / "final-world.json").write_text(world.model_dump_json(indent=2), encoding="utf-8")
    write_json(output / "metrics.json", metrics)
    write_csv(output / "tasks.csv", rows, None if rows else ["task_id", "observed_pickup_s", "observed_completion_s"])
    write_csv(output / "vehicles.csv", trips, None if trips else ["vehicle_id", "type", "duration_s"])
    merged_events = sorted([*(runner.events if runner else []), *observations], key=lambda e: e["sim_time_s"])
    (output / "events.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in merged_events), encoding="utf-8",
    )
    if runner and record:
        from rescueflow.dashboard.network import network_geometry
        write_json(output / "replay.json", {
            "source": f"{meta['suite']}/{meta['scenario']}/{meta['seed']}/{policy}", "inputs_sha256": checksum,
            "geometry": network_geometry(travel, "grid"), "frames": runner.frames, "events": merged_events,
        })
    return metrics
