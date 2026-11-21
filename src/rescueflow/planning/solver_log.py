"""Append-only JSONL audit trail for both planners (docs/DEVELOPMENT_PLAN.md
prompt 2 item 5: "Log solver objective, selected actions, rejected
infeasible candidates and ETA assumptions"). One line per event; never
rewritten, so a run's full reasoning trail survives even if a later run
appends more. Consumed by `docs/RUNBOOK.md`'s `sim export-run`, and
directly `tail`-able during a demo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_SOLVER_LOG_PATH = "simulation/state/solver_log.jsonl"


class SolverLog:
    def __init__(self, path: str = DEFAULT_SOLVER_LOG_PATH) -> None:
        self.path = path

    def _write(self, record: dict) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        record = {"wall_time_s": time.time(), **record}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def candidate_evaluated(
        self, policy: str, profile: str, task_id: str, vehicle_id: str, feasible: bool, added_travel_s: float | None
    ) -> None:
        self._write({
            "kind": "candidate_evaluated", "policy": policy, "profile": profile, "task_id": task_id,
            "vehicle_id": vehicle_id, "feasible": feasible, "added_travel_s": added_travel_s,
        })

    def candidate_rejected(self, policy: str, profile: str, task_id: str, reason: str) -> None:
        self._write({
            "kind": "candidate_rejected", "policy": policy, "profile": profile, "task_id": task_id, "reason": reason,
        })

    def plan_selected(self, proposal_id: str, policy: str, profile: str, score: dict, eta_assumption: str) -> None:
        self._write({
            "kind": "plan_selected", "proposal_id": proposal_id, "policy": policy, "profile": profile,
            "score": score, "eta_assumption": eta_assumption,
        })

    def solve_budget_exhausted(self, policy: str, profile: str, elapsed_s: float, budget_s: float) -> None:
        self._write({
            "kind": "solve_budget_exhausted", "policy": policy, "profile": profile,
            "elapsed_s": elapsed_s, "budget_s": budget_s,
        })
