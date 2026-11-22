"""Matched Monte Carlo comparison of B0, A1 and A2 (definitions of 2026-11-22).

B0  nearest free unit, live-traffic routing, normal signals, no AI
A1  feasible fleet-wide options ranked by the Decisions API, normal signals
A2  A1 plus green-wave priority for critical legs
R1  ablation: the same options as A1, picked by the deterministic rule (no AI).
    A1 vs R1 is what the AI itself adds; B0 vs R1 is what the optimizer adds.

These are NOT the historical A1/A2/A3 of docs/EXPERIMENTS.md (old A2 was
B0 + signals). Automatic execution of the AI's first choice exists only here.

    python -m rescueflow.benchmark.montecarlo run --scenarios 100 --jobs 12
    python -m rescueflow.benchmark.montecarlo report
    python -m rescueflow.benchmark.montecarlo replay --seed 12 --policy A2

Runs are resumable: a finished run directory is never repeated.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean, median

from rescueflow.approval.service import DefaultApprovalService
from rescueflow.benchmark.inputs import digest, write_json
from rescueflow.benchmark.runner import _trip_rows, percentile90
from rescueflow.benchmark.scenarios import HORIZON_S, RESPONSE_CAP_S, RESPONSE_TARGET_S, prepare
from rescueflow.decision.agent import SOURCE_AI, SOURCE_FALLBACK, DecisionAgent, fallback_key
from rescueflow.decision.alternatives import generate_alternatives
from rescueflow.dispatch.notification import MockDispatchNotificationAdapter
from rescueflow.events.apply import apply_event
from rescueflow.events.interpreter import RuleBasedEventInterpreter
from rescueflow.events.types import EventDraft, ValidationError
from rescueflow.models.core import EventType, TaskKind, TaskStatus, WorldState
from rescueflow.planning.baseline import BaselinePlanner
from rescueflow.sumo.controller import TraciSimulationController
from rescueflow.travel import SumoTravelTimeProvider

POLICIES = ("B0", "A1", "A2")
ALL_POLICIES = (*POLICIES, "R1")
ROOT = Path("experiments/mc")


class FallbackUsed(RuntimeError):
    """The AI was unavailable; the run is not a measurement of an AI policy."""


class Run:
    def __init__(self, folder: Path, output: Path, policy: str, agent: DecisionAgent | None, gui: bool = False) -> None:
        self.manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        net = folder.parents[1] / "grid.net.xml"
        for name, expected in self.manifest["files"].items():
            if digest(net if name == net.name else folder / name) != expected:
                raise ValueError(f"Input changed: {name}")
        self.policy, self.agent = policy, agent
        self.world = WorldState.model_validate_json((folder / "world.json").read_bytes())
        self.travel = SumoTravelTimeProvider(str(net))
        self.controller = TraciSimulationController(
            str(folder / "run.sumocfg"), str(net), str(output / "unused.state.xml.gz"), self.manifest["seed"],
            allow_teleport=False, observations_dir=output, junction_stops=True, gui=gui)
        self.controller.defer_junction_reroutes = True
        self.controller.green_wave.enabled = False
        self.events: list[dict] = []
        self.decisions: list[dict] = []
        self.reported: dict[str, float] = {}
        self.changed = 0
        self.on_step = None

    def log(self, kind: str, **fields) -> None:
        self.events.append({"kind": kind, "sim_time_s": self.world.sim_time_s, **fields})

    # -- exogenous events ----------------------------------------------------

    def apply(self, event: dict) -> bool | None:
        """True = applied, False = void in this state (logged), None = retry next second."""
        world, kind = self.world, event["type"]
        draft = EventDraft(type=EventType(kind), sim_time_s=world.sim_time_s, vehicle_id=event.get("vehicle_id"),
                           task_id=event.get("task_id"), payload=event.get("payload", {}))
        unit = world.vehicles.get(event.get("vehicle_id") or "")
        if kind == "vehicle_breakdown":
            if (unit.sumo_edge_id or "").startswith(":"):
                return None
            draft.equipment_ids_reported_stranded = list(unit.equipment_aboard)
        validated = RuleBasedEventInterpreter().validate_event(draft, world)
        if isinstance(validated, ValidationError):
            self.log("event_void", event=event, reason=validated.code)
            return False
        if kind == "vehicle_breakdown":
            self.controller.freeze_vehicle_and_locate(world, unit.id)
        affected = []
        if kind == "road_closure":
            edge = event["payload"]["edge_id"]
            affected = [v for v, seen in self.controller.live_telemetry(world)["fleet"].items() if edge in seen["route"][1:]]
            self.controller.apply_road_closure_physically(world, edge)
        apply_event(world, validated)
        if kind == "vehicle_delay":
            self.controller.hold_vehicle(world, unit.id, event["payload"]["delay_s"])
        if kind == "destination_changed":
            affected = [v.id for v in world.vehicles.values() if any(
                s.task_id == event["task_id"] and s.service_completed_at_s is None for s in v.route_stops)]
        if affected:
            # Forced by the event itself, so identical for every policy: units
            # drive around the closure or to the new destination at once.
            self.controller.apply_committed_plan(world, [v for v in affected if world.vehicles[v].route_stops])
        if kind == "new_urgent_task":
            self.reported[event["payload"]["task_id"]] = world.sim_time_s
        self.log("event_applied", event=event, affected=affected)
        return True

    # -- dispatch ------------------------------------------------------------

    def choose(self):
        world = self.world
        self.controller.use_live_traffic(self.travel)
        event = world.events[-1]
        if self.policy == "B0":
            planning = world.model_copy(deep=True)
            self.travel.set_closed_edges(world.closed_edges)
            for vehicle in planning.vehicles.values():
                edge = vehicle.sumo_edge_id
                if edge and not edge.startswith(":"):
                    vehicle.node_id = self.travel.net.getEdge(edge).getToNode().getID()
            plan = BaselinePlanner(self.travel, solve_budget_s=30, nearest_available_emergency=True
                                   ).baseline_repair(planning, event)
            return plan if plan.feasibility.feasible else None

        def route_check(current, proposal):
            self.controller.preview_routes(current, {p.vehicle_id: p.stops for p in proposal.vehicle_plans})

        options, notes = generate_alternatives(world, self.travel, event, budget_s=30.0, route_check=route_check)
        if not options:
            self.log("no_feasible_option", notes=notes[:5])
            return None
        if self.policy == "R1":
            return min(options, key=fallback_key).proposal
        result = self.agent.rank(world, event, options)
        chosen = next(o for o in options if o.option_id == result.recommended)
        self.decisions.append({
            "sim_time_s": world.sim_time_s, "source": result.source, "model": result.model,
            "latency_ms": result.latency_ms, "cache_hit": result.cache_hit, "error": result.error,
            "chosen": chosen.strategy, "option_id": chosen.option_id, "options": [o.strategy for o in options],
            "rule_choice": next((o.strategy for o in options if o.option_id == result.deterministic_choice), None),
            "probabilities": result.probabilities, "usage": result.usage})
        if result.source == SOURCE_FALLBACK:
            raise FallbackUsed(result.error or "unknown")
        return chosen.proposal

    def dispatch(self) -> None:
        started = time.perf_counter()
        plan = self.choose()
        elapsed = (time.perf_counter() - started) * 1000
        if plan is None:
            self.log("plan_infeasible", decision_ms=elapsed)
            return
        candidate = self.world.model_copy(deep=True)
        before = {k: t.assigned_vehicle_id for k, t in candidate.tasks.items()}
        result = DefaultApprovalService(notifier=MockDispatchNotificationAdapter()).approve(
            candidate, plan, approved_by="benchmark-auto", idempotency_key=f"d{len(self.events)}")
        if not result.committed:
            self.log("approval_rejected", reason=result.rejection_reason, decision_ms=elapsed)
            return
        touched = [p.vehicle_id for p in plan.vehicle_plans]
        self.controller.preview_routes(candidate, {v: candidate.vehicles[v].route_stops for v in touched})
        self.controller.apply_committed_plan(candidate, touched)
        self.world = candidate
        self.controller.green_wave.enabled = self.policy == "A2"
        self.changed += sum(bool(before.get(k)) and t.assigned_vehicle_id != before[k] for k, t in candidate.tasks.items())
        self.log("plan_committed", units=touched, unserved=plan.unserved_task_ids, decision_ms=elapsed)

    def execute(self) -> None:
        pending = list(self.manifest["events"])
        due: list[float] = []
        delay = self.manifest["approval_delay_s"] + self.manifest["mobilization_delay_s"]
        self.controller.load_scenario(self.world, keep_connected=True)
        while self.world.sim_time_s < HORIZON_S:
            now = self.world.sim_time_s
            for event in pending[:]:
                if event["at"] > now:
                    continue
                outcome = self.apply(event)
                if outcome is None:
                    continue
                pending.remove(event)
                if outcome:
                    due.append(now + delay)
            if due and now >= due[0]:
                due = [d for d in due if d > now]
                self.dispatch()
            self.controller.step(self.world, capture_positions=False)
            if self.on_step is not None:
                self.on_step(self)
        for event in pending:
            self.log("event_never_applied", event=event)

    # -- observation ---------------------------------------------------------

    def metrics(self, output: Path, failures: list[str]) -> dict:
        log = self.controller.event_log
        calls = {}
        for event in self.manifest["events"]:
            if event["type"] != "new_urgent_task":
                continue
            task_id = event["payload"]["task_id"]
            reported = self.reported.get(task_id)
            reach = next((e for e in log if e["kind"] == "stop_begin" and e.get("task_id") == task_id
                          and e.get("stop_kind") == "pickup"), None)
            task = self.world.tasks.get(task_id)
            response = None
            if reach and reported is not None:
                node = self.travel.net.getNode(event["payload"]["pickup_node"]).getCoord()
                unit = self.world.vehicles[reach["vehicle_id"]]
                # The grid junction stop line is at most this far from the node.
                if math.dist(node, reach["xy"]) > 30 or set(task.required_capabilities) - set(unit.capabilities):
                    failures.append(f"{task_id}: wrong place or crew")
                else:
                    response = reach["sim_time_s"] - reported
            calls[task_id] = {"priority": event["payload"]["priority"], "response_s": response,
                              "capped_s": min(response, RESPONSE_CAP_S) if response is not None else RESPONSE_CAP_S}
        tasks = [t for t in self.world.tasks.values() if not t.superseded_by]
        done = {e["task_id"]: e["sim_time_s"] for e in log if e["kind"] == "stop_completed" and e.get("stop_kind") == "dropoff"}
        late = [max(0.0, done.get(t.id, HORIZON_S) - t.latest_delivery_s) for t in tasks if t.latest_delivery_s is not None]
        kit = [t for t in tasks if t.kind in (TaskKind.equipment_delivery, TaskKind.equipment_recovery)]
        trips = _trip_rows(output / "tripinfo.xml") if (output / "tripinfo.xml").exists() else []
        civilian = [t for t in trips if t["type"] == "civilian"]
        kinds = Counter(e["kind"] for e in log)
        if kinds["missing_sumo_stop"]:
            failures.append(f"missing_sumo_stop: {kinds['missing_sumo_stop']}")
        if kinds["teleport_started"]:
            failures.append(f"{kinds['teleport_started']} teleports")
        warnings = (output / "warnings.log").read_text(encoding="utf-8", errors="replace") if (output / "warnings.log").exists() else ""
        sources = Counter(d["source"] for d in self.decisions)
        ranked = [d for d in self.decisions if d["source"] == SOURCE_AI]
        return {
            "seed": self.manifest["seed"], "policy": self.policy, "density": self.manifest["density"],
            "event_types": sorted({e["type"] for e in self.manifest["events"]}),
            "valid": not failures, "failures": failures, "calls": calls,
            "ambulance_collisions": sum(e["kind"] == "collision" and e["vehicle_id"] in self.world.vehicles for e in log),
            "collisions": kinds["collision"], "missing_stops": kinds["missing_sumo_stop"],
            "insertion_errors": warnings.count("will not be able to depart"),
            "critical_unreached": sum(c["priority"] == "critical" and c["response_s"] is None for c in calls.values()),
            "mean_task_lateness_s": mean(late) if late else None,
            "equipment_total": len(kit), "equipment_completed": sum(t.status == TaskStatus.completed for t in kit),
            "reassignments": self.changed, "void_events": sum(e["kind"] == "event_void" for e in self.events),
            "events_never_applied": sum(e["kind"] == "event_never_applied" for e in self.events),
            "rejected_plans": sum(e["kind"] in ("approval_rejected", "plan_infeasible", "no_feasible_option") for e in self.events),
            "civilian_finished": sum(t["arrival_s"] >= 0 for t in civilian),
            "civilian_planned": self.manifest["civilian_departures"],
            # All entered trips, finished or not, so gridlock is not hidden.
            "civilian_mean_time_loss_s": mean(t["time_loss_s"] for t in civilian) if civilian else None,
            "priority_requests": kinds["priority_requested"], "priority_restored": kinds["restored"],
            "phases_shortened": kinds["phase_shortened"],
            "seconds_shortened": sum(e.get("shortened_by_s", 0) for e in log if e["kind"] == "phase_shortened"),
            "decisions": len(self.decisions), "decision_sources": dict(sources),
            "ai_differs_from_rule": sum(d["chosen"] != d["rule_choice"] for d in ranked),
            "ai_choice": dict(Counter(d["chosen"] for d in ranked)),
            "ai_latency_ms": [d["latency_ms"] for d in ranked if not d["cache_hit"]],
            "ai_input_tokens": sum((d["usage"] or {}).get("input_tokens", 0) for d in ranked if not d["cache_hit"]),
            "decision_ms": [e["decision_ms"] for e in self.events if "decision_ms" in e],
        }


def worker(root: Path, seed: int, policy: str) -> int:
    from dotenv import load_dotenv

    load_dotenv(".env")
    output = root / "runs" / f"{seed:04d}" / policy
    output.mkdir(parents=True, exist_ok=True)
    folder = prepare(root, seed)
    agent = None
    if policy in ("A1", "A2"):
        agent = DecisionAgent(cache_dir=root / "ai-cache", retries=4, timeout_s=30.0, max_calls=40)
        if agent.client is None:
            raise SystemExit(f"AI unavailable: {agent.unavailable_reason}")
    run = Run(folder, output, policy, agent)
    failures: list[str] = []
    fallback = None
    try:
        run.execute()
    except FallbackUsed as exc:
        fallback = str(exc)
    except Exception as exc:  # noqa: BLE001 -- retained as a failed run
        failures.append(f"{type(exc).__name__}: {exc}"[:300])
    finally:
        run.controller.close()
    if fallback is not None:
        # Not an outcome of the policy: leave no result so a resume retries it.
        (output / "ai-unavailable.txt").write_text(fallback, encoding="utf-8")
        return 3
    (output / "ai-unavailable.txt").unlink(missing_ok=True)
    result = run.metrics(output, failures)
    result["git_sha"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    with (output / "events.jsonl").open("w", encoding="utf-8") as stream:
        for event in sorted([*run.events, *run.controller.event_log], key=lambda e: e["sim_time_s"]):
            stream.write(json.dumps(event, default=str) + "\n")
    (output / "decisions.json").write_text(json.dumps(run.decisions), encoding="utf-8")
    (output / "routes.xml").unlink(missing_ok=True)
    (output / "tripinfo.xml").unlink(missing_ok=True)
    write_json(output / "metrics.json", result)
    return 0


def launch(root: Path, scenarios: range, policies: tuple[str, ...], jobs: int) -> None:
    for seed in scenarios:
        prepare(root, seed)
    todo = [(s, p) for s in scenarios for p in policies if not (root / "runs" / f"{s:04d}" / p / "metrics.json").exists()]
    print(f"{len(todo)} runs to do, {len(scenarios) * len(policies) - len(todo)} already finished", flush=True)
    codes: Counter = Counter()
    started = time.time()

    def one(item):
        seed, policy = item
        log = root / "runs" / f"{seed:04d}" / policy / "process.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as stream:
            return subprocess.run(
                [sys.executable, "-m", "rescueflow.benchmark.montecarlo", "worker", "--root", str(root),
                 "--seed", str(seed), "--policy", policy],
                stdout=stream, stderr=subprocess.STDOUT, timeout=1800, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            ).returncode

    with ThreadPoolExecutor(jobs) as pool:
        futures = {pool.submit(one, item): item for item in todo}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                codes[future.result()] += 1
            except subprocess.TimeoutExpired:
                codes["timeout"] += 1
            if index % 25 == 0 or index == len(todo):
                print(f"{index}/{len(todo)} in {time.time() - started:.0f}s exit codes {dict(codes)}", flush=True)
            # Stop spending when the AI itself is down rather than log thousands of misses.
            if codes[3] >= 20 and codes[3] > 0.5 * index:
                print("AI unavailable repeatedly; stopping. Resume with the same command.", flush=True)
                for other in futures:
                    other.cancel()
                break


def replay(root: Path, seed: int, policy: str, *, gui: bool, delay_ms: int) -> int:
    """Re-execute a recorded run from its frozen inputs and recorded AI answers.

    The AI is never called: a decision that was not recorded stops the replay.
    Afterwards the observed responses are compared with the recorded ones."""
    recorded_path = root / "runs" / f"{seed:04d}" / policy / "metrics.json"
    recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
    agent = DecisionAgent(cache_dir=root / "ai-cache", enabled=False) if policy in ("A1", "A2") else None
    folder = prepare(root, seed)
    print(f"Scenario {seed} {policy}: {recorded['density']} traffic, events {', '.join(recorded['event_types'])}", flush=True)
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="rescueflow-replay-") as temporary:
        output = Path(temporary)
        run = Run(folder, output, policy, agent, gui=gui)
        shown = {"events": 0, "log": 0}

        def narrate(current: Run) -> None:
            if gui and delay_ms:
                time.sleep(delay_ms / 1000)
            for event in current.events[shown["events"]:]:
                if event["kind"] == "event_applied":
                    print(f"  {event['sim_time_s']:6.0f}s  EVENT {event['event']['type']}", flush=True)
                elif event["kind"] == "plan_committed":
                    print(f"  {event['sim_time_s']:6.0f}s  plan committed for {', '.join(event['units']) or 'no unit'}", flush=True)
            shown["events"] = len(current.events)
            for event in current.controller.event_log[shown["log"]:]:
                if event["kind"] == "stop_begin" and str(event.get("task_id")).startswith("CALL-") and event["stop_kind"] == "pickup":
                    print(f"  {event['sim_time_s']:6.0f}s  {event['vehicle_id']} reaches {event['task_id']}", flush=True)
                elif event["kind"] == "phase_shortened":
                    print(f"  {event['sim_time_s']:6.0f}s  signal {event['tls_id']} shortened for {event['vehicle_id']}", flush=True)
            shown["log"] = len(current.controller.event_log)

        run.on_step = narrate
        try:
            run.execute()
        finally:
            run.controller.close()
        observed = run.metrics(output, failures)
    same = observed["calls"] == recorded["calls"]
    print("Replay reproduces the recorded responses." if same else
          f"Replay DIFFERS from the record.\n  recorded {recorded['calls']}\n  replayed {observed['calls']}")
    return 0 if same else 4


# -- report --------------------------------------------------------------------


def bootstrap(differences: list[float], draws: int = 4000) -> list[float]:
    rng = random.Random(20261122)
    n = len(differences)
    means = sorted(mean(rng.choices(differences, k=n)) for _ in range(draws))
    return [means[int(0.025 * draws)], means[int(0.975 * draws) - 1]]


def scenario_score(row: dict) -> float:
    return mean(c["capped_s"] for c in row["calls"].values())


def report(root: Path) -> dict:
    rows: dict[str, dict[int, dict]] = {p: {} for p in ALL_POLICIES}
    for path in root.glob("runs/*/*/metrics.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        rows[row["policy"]][row["seed"]] = row
    summary = {"policy_set": "2026-11-22 (B0 / A1 AI dispatch / A2 AI dispatch + signals)",
               "response_cap_s": RESPONSE_CAP_S, "policies": {}, "pairs": []}
    for policy, found in rows.items():
        valid = [r for r in found.values() if r["valid"]]
        calls = [c for r in valid for c in r["calls"].values()]
        critical = [c for c in calls if c["priority"] == "critical"]
        reached = sorted(c["response_s"] for c in calls if c["response_s"] is not None)
        sources: Counter = Counter()
        choices: Counter = Counter()
        for r in valid:
            sources.update(r["decision_sources"])
            choices.update(r["ai_choice"])
        get = lambda key: [r[key] for r in valid if r[key] is not None]  # noqa: E731
        summary["policies"][policy] = {
            "completed_runs": len(found), "valid_runs": len(valid),
            "failures": dict(Counter(f.split(":")[0][:40] for r in found.values() for f in r["failures"])),
            "calls": len(calls), "reached": len(reached),
            "critical_unreached": sum(c["response_s"] is None for c in critical), "critical_calls": len(critical),
            "mean_capped_s": mean(c["capped_s"] for c in calls) if calls else None,
            "critical_mean_capped_s": mean(c["capped_s"] for c in critical) if critical else None,
            "mean_reached_s": mean(reached) if reached else None, "median_reached_s": median(reached) if reached else None,
            "p90_reached_s": percentile90(reached),
            "within_target_pct": 100 * sum(t <= RESPONSE_TARGET_S for t in reached) / len(calls) if calls else None,
            "mean_task_lateness_s": mean(get("mean_task_lateness_s")) if valid else None,
            "equipment": [sum(get("equipment_completed")), sum(get("equipment_total"))],
            "reassignments": sum(get("reassignments")),
            "civilian_mean_time_loss_s": mean(get("civilian_mean_time_loss_s")) if valid else None,
            "ambulance_collisions": sum(get("ambulance_collisions")), "insertion_errors": sum(get("insertion_errors")),
            "missing_stops": sum(get("missing_stops")),
            "phases_shortened": sum(get("phases_shortened")), "seconds_shortened": sum(get("seconds_shortened")),
            "decision_sources": dict(sources), "ai_choice": dict(choices),
            "ai_differs_from_rule": sum(get("ai_differs_from_rule")),
            "ai_latency_ms_median": median([x for r in valid for x in r["ai_latency_ms"]] or [0]),
            "ai_input_tokens": sum(get("ai_input_tokens")),
        }
    for reference, candidate in (("B0", "A1"), ("A1", "A2"), ("B0", "A2"), ("B0", "R1"), ("R1", "A1")):
        both = sorted(set(rows[reference]) & set(rows[candidate]))
        valid = [s for s in both if rows[reference][s]["valid"] and rows[candidate][s]["valid"]]
        if len(valid) < 2:
            continue
        saved = [scenario_score(rows[reference][s]) - scenario_score(rows[candidate][s]) for s in valid]
        # Sensitivity: keep every pair; a failed run scores the cap for all calls.
        score = lambda r: scenario_score(r) if r["valid"] else float(RESPONSE_CAP_S)  # noqa: E731
        kept = [score(rows[reference][s]) - score(rows[candidate][s]) for s in both]
        base = mean(scenario_score(rows[reference][s]) for s in valid)
        summary["pairs"].append({
            "reference": reference, "candidate": candidate, "matched_pairs": len(both), "valid_pairs": len(valid),
            "mean_seconds_saved": mean(saved), "ci95": bootstrap(saved), "relative_pct": 100 * mean(saved) / base,
            "better": sum(d > 1 for d in saved), "same": sum(abs(d) <= 1 for d in saved), "worse": sum(d < -1 for d in saved),
            "sensitivity_failed_runs_at_cap": {"mean_seconds_saved": mean(kept), "ci95": bootstrap(kept)},
            "by_density": {d: mean(scenario_score(rows[reference][s]) - scenario_score(rows[candidate][s]) for s in valid
                                   if rows[reference][s]["density"] == d)
                           for d in ("light", "moderate", "heavy") if any(rows[reference][s]["density"] == d for s in valid)},
        })
    write_json(root / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--scenarios", type=int, default=10)
    go.add_argument("--policies", default=",".join(POLICIES))
    go.add_argument("--jobs", type=int, default=8)
    one = sub.add_parser("worker")
    one.add_argument("--seed", type=int, required=True)
    one.add_argument("--policy", choices=ALL_POLICIES, required=True)
    again = sub.add_parser("replay")
    again.add_argument("--seed", type=int, required=True)
    again.add_argument("--policy", choices=ALL_POLICIES, required=True)
    again.add_argument("--headless", action="store_true")
    again.add_argument("--delay", type=int, default=40, help="milliseconds per simulated second")
    for item in (go, one, again, sub.add_parser("report")):
        item.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.command == "worker":
        sys.exit(worker(args.root, args.seed, args.policy))
    if args.command == "replay":
        sys.exit(replay(args.root, args.seed, args.policy, gui=not args.headless, delay_ms=args.delay))
    if args.command == "run":
        args.root.mkdir(parents=True, exist_ok=True)
        launch(args.root, range(args.scenarios), tuple(args.policies.split(",")), args.jobs)
    print(json.dumps(report(args.root), indent=2))


if __name__ == "__main__":
    main()
