"""Central recommendation agent: the OpenAI Decisions API ranks feasible options.

The deterministic optimizer supplies feasible alternatives and their
calculated metrics. This agent asks `gpt-6-luna` one `choice` question whose
allowed values are exactly the candidate option IDs, and ranks the options
by the returned per-option probabilities. The model cannot add options,
change routes or invent travel times.

If the API is unavailable, errors, times out or refuses, the agent returns a
clearly labeled deterministic ranking (`source="deterministic-fallback"`)
and records the error. Responses are cached by a fingerprint of the exact
request (model, prompt version, situation and options) for reproducible
replays.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rescueflow.decision.alternatives import Alternative
from rescueflow.models.core import Event, TaskStatus, WorldState

MODEL = "gpt-6-luna"
PROMPT_VERSION = "rescueflow-decision-v1"
SOURCE_AI = "openai-decisions"
SOURCE_SINGLE = "single-feasible-option"
SOURCE_FALLBACK = "deterministic-fallback"

INSTRUCTIONS = (
    "You are the recommendation engine of an ambulance dispatch centre (synthetic simulation). "
    "Each option is a feasible plan produced by a deterministic optimizer; all times are its "
    "calculated estimates in seconds. Choose the option a careful dispatcher should approve. "
    "Prioritise reaching critical patients quickly and leaving no critical patient unserved; then "
    "avoid late high-priority work; then prefer fewer changes to existing assignments. Equipment "
    "that another task depends on must still be delivered. Use only the numbers given."
)


@dataclass
class Recommendation:
    source: str
    ranking: list[str]
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    latency_ms: float | None = None
    cache_hit: bool = False
    error: str | None = None
    usage: dict | None = None
    fingerprint: str | None = None
    explanation: str = ""
    deterministic_choice: str | None = None
    """What the fallback rule would have picked (to measure what the AI changes)."""

    @property
    def recommended(self) -> str | None:
        return self.ranking[0] if self.ranking else None

    @property
    def is_ai(self) -> bool:
        return self.source == SOURCE_AI

    def as_dict(self) -> dict:
        return {**asdict(self), "recommended": self.recommended}


def fallback_key(option: Alternative) -> tuple:
    """Deterministic ranking: unserved critical, worst critical ETA, lateness, churn."""
    m = option.metrics
    worst = m["worst_critical_response_eta_s"]
    return (
        m["unserved_critical"], len(m["unserved_task_ids"]), worst if worst is not None else 0.0,
        m["critical_tardiness_s"], m["other_weighted_lateness_s"], len(m["reassigned_task_ids"]),
        option.option_id,
    )


def situation(world: WorldState, event: Event | None) -> dict:
    open_tasks = [
        {"id": t.id, "kind": t.kind.value, "priority": t.priority.value, "status": t.status.value,
         "assigned_unit": t.assigned_vehicle_id, "needs": t.required_capabilities,
         "equipment": t.required_asset_ids}
        for t in world.tasks.values()
        if t.status not in (TaskStatus.completed, TaskStatus.interrupted, TaskStatus.unserved)
    ]
    fleet = [
        {"id": v.id, "status": v.status.value, "capabilities": v.capabilities,
         "equipment_aboard": v.equipment_aboard}
        for v in world.vehicles.values()
    ]
    return {
        "sim_time_s": round(world.sim_time_s, 1),
        "event": {"type": event.type.value, "summary": event.impact_summary, "payload": event.payload}
        if event else None,
        "fleet": fleet, "open_tasks": open_tasks, "closed_roads": sorted(world.closed_edges),
    }


def _choice_description(option: Alternative) -> str:
    m = option.metrics
    worst = m["worst_critical_response_eta_s"]
    parts = [option.description]
    parts.append(f"worst critical response {worst:.0f}s" if worst is not None else "no open critical pickup")
    parts.append(f"unserved {len(m['unserved_task_ids'])} (critical {m['unserved_critical']})")
    parts.append(f"estimated late {len(m['estimated_deadline_misses'])}")
    parts.append(f"reassigned {len(m['reassigned_task_ids'])}")
    return "; ".join(parts)[:500]


def explain(option: Alternative, options: list[Alternative], source: str, probability: float | None) -> str:
    """Factual summary from calculated metrics. No invented reasoning."""
    m = option.metrics
    who = {SOURCE_AI: "AI recommendation", SOURCE_SINGLE: "Only feasible option",
           SOURCE_FALLBACK: "Deterministic fallback (AI unavailable)"}[source]
    if probability is not None and source == SOURCE_AI:
        who += f" (model probability {probability:.0%})"
    lines = [f"{who}: option {option.option_id}, {option.description.lower()}."]
    worst = m["worst_critical_response_eta_s"]
    if worst is not None:
        others = [o.metrics["worst_critical_response_eta_s"] for o in options
                  if o is not option and o.metrics["worst_critical_response_eta_s"] is not None]
        compare = f" (other options: {', '.join(f'{v:.0f}s' for v in others)})" if others else ""
        lines.append(f"Worst estimated critical response {worst:.0f}s{compare}.")
    lines.append(f"{len(m['unserved_task_ids'])} unserved, {len(m['estimated_deadline_misses'])} estimated late, "
                 f"{len(m['reassigned_task_ids'])} existing task(s) reassigned.")
    if option.signal_priority_units:
        lines.append(f"Signal priority eligible: {', '.join(option.signal_priority_units)}.")
    return " ".join(lines)


class DecisionAgent:
    """Ranks alternatives. Thread-safe; one instance per process is enough."""

    def __init__(self, *, model: str = MODEL, cache_dir: Path | None = None, enabled: bool = True,
                 timeout_s: float = 20.0, retries: int = 2, max_calls: int | None = None,
                 min_interval_s: float = 0.0, log_path: Path | None = None) -> None:
        self.model = model
        self.cache_dir = cache_dir
        self.timeout_s = timeout_s
        self.retries = retries
        self.max_calls = max_calls
        self.min_interval_s = min_interval_s
        self.log_path = log_path
        self.calls = 0
        self._last_call = 0.0
        self._lock = threading.Lock()
        self.client = None
        self.unavailable_reason = None
        if not enabled:
            self.unavailable_reason = "AI ranking disabled"
        elif not os.getenv("OPENAI_API_KEY"):
            self.unavailable_reason = "OPENAI_API_KEY is not set"
        else:
            try:
                from openai import OpenAI
            except ImportError:
                self.unavailable_reason = "openai package is not installed"
            else:
                # gzip only: httpx2 and older brotlicffi disagree on Brotli decoding.
                self.client = OpenAI(timeout=timeout_s, max_retries=0,
                                     default_headers={"Accept-Encoding": "gzip, deflate"})
                if not hasattr(self.client, "decisions"):
                    self.client, self.unavailable_reason = None, "installed openai SDK has no Decisions API"

    @property
    def label(self) -> str:
        return f"OpenAI Decisions · {self.model}" if self.client else f"Deterministic fallback · {self.unavailable_reason}"

    def request(self, world: WorldState, event: Event | None, options: list[Alternative]) -> dict:
        return {
            "model": self.model,
            "input": [
                {"role": "developer", "content": INSTRUCTIONS},
                {"role": "user", "content": [{"type": "input_json", "data": {
                    "prompt_version": PROMPT_VERSION, "situation": situation(world, event),
                    "options": [o.summary() for o in options],
                }}]},
            ],
            "questions": [{
                "type": "choice", "name": "recommended_option",
                "instructions": "Which option should the dispatcher approve?",
                "choices": [{"value": o.option_id, "description": _choice_description(o)} for o in options],
            }],
        }

    def rank(self, world: WorldState, event: Event | None, options: list[Alternative]) -> Recommendation:
        if not options:
            return Recommendation(source=SOURCE_FALLBACK, ranking=[], error="no feasible option")
        if len(options) == 1:
            only = options[0]
            return Recommendation(source=SOURCE_SINGLE, ranking=[only.option_id],
                                  explanation=explain(only, options, SOURCE_SINGLE, None))
        request = self.request(world, event, options)
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
        cached = self._cache_get(fingerprint)
        if cached is not None:
            result = Recommendation(**{**cached, "cache_hit": True})
        else:
            result = self._ask(request, options)
            result.fingerprint = fingerprint
            if result.is_ai:
                self._cache_put(fingerprint, result)
        result.deterministic_choice = min(options, key=fallback_key).option_id
        by_id = {o.option_id: o for o in options}
        if result.recommended in by_id:
            result.explanation = explain(by_id[result.recommended], options, result.source,
                                         result.probabilities.get(result.recommended))
        self._log(world, options, result)
        return result

    def _fallback(self, options: list[Alternative], error: str | None) -> Recommendation:
        ranking = [o.option_id for o in sorted(options, key=fallback_key)]
        return Recommendation(source=SOURCE_FALLBACK, ranking=ranking, error=error)

    def _ask(self, request: dict, options: list[Alternative]) -> Recommendation:
        if self.client is None:
            return self._fallback(options, self.unavailable_reason)
        with self._lock:
            if self.max_calls is not None and self.calls >= self.max_calls:
                return self._fallback(options, f"AI call budget of {self.max_calls} reached")
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            self.calls += 1
        error = None
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                decision = self.client.decisions.create(**request)
            except Exception as exc:  # noqa: BLE001 -- recorded, never hidden
                status = getattr(exc, "status_code", None)
                error = f"{type(exc).__name__}{f' {status}' if status else ''}"
                if status in (400, 401, 403, 404) or attempt == self.retries:
                    break
                time.sleep(min(8.0, 1.5 * 2 ** attempt))
                continue
            latency = (time.perf_counter() - started) * 1000
            answer = decision.answers[0] if decision.answers else None
            if answer is None or answer.type != "choice":
                error = f"model returned {getattr(answer, 'type', 'no answer')}"
                break
            valid = {o.option_id for o in options}
            probabilities = {str(p.value): float(p.probability) for p in (answer.probabilities or [])
                             if str(p.value) in valid}
            if answer.choice not in valid:
                error = f"model chose unknown option {answer.choice!r}"
                break
            rule = {o.option_id: fallback_key(o) for o in options}
            ranking = sorted(valid, key=lambda oid: (oid != answer.choice, -probabilities.get(oid, 0.0), rule[oid]))
            usage = decision.usage.model_dump() if getattr(decision, "usage", None) else None
            return Recommendation(source=SOURCE_AI, ranking=ranking, probabilities=probabilities,
                                  model=decision.model, latency_ms=round(latency, 1), usage=usage)
        return self._fallback(options, error)

    def _cache_get(self, fingerprint: str) -> dict | None:
        if self.cache_dir is None:
            return None
        path = self.cache_dir / fingerprint[:2] / f"{fingerprint}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _cache_put(self, fingerprint: str, result: Recommendation) -> None:
        if self.cache_dir is None:
            return
        path = self.cache_dir / fingerprint[:2] / f"{fingerprint}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(result).items() if k not in ("cache_hit", "explanation")}
        path.write_text(json.dumps(data), encoding="utf-8")

    def _log(self, world: WorldState, options: list[Alternative], result: Recommendation) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"sim_time_s": world.sim_time_s, "plan_revision": world.plan_revision,
                  "options": [o.summary() for o in options], **result.as_dict()}
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, default=str) + "\n")
