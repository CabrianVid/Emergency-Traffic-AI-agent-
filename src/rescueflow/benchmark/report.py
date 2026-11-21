"""Build auditable tables and publication-ready charts from run files."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

from rescueflow.benchmark.inputs import POLICIES, digest, write_json
from rescueflow.benchmark.replay import render_replay
from rescueflow.benchmark.runner import statistics, write_csv

CONTRASTS = (
    ("B0", "A1", "Optimization"), ("B0", "A2", "Signal priority"),
    ("A1", "A3", "Signal priority on optimized routes"), ("B0", "A3", "Combined"),
)


def _mean(items):
    values = [item for item in items if item is not None]
    return mean(values) if values else None


def _relative_change(before, after):
    return 100 * (before - after) / before if before is not None and after is not None and before > 0 else None


def _bootstrap(deltas):
    if len(deltas) < 20:
        return None, None
    rng = random.Random(404)
    draws = sorted(mean(rng.choices(deltas, k=len(deltas))) for _ in range(4000))
    return draws[100], draws[3899]


def _condition(runs: list[dict]) -> dict:
    eligible = [run for run in runs if run["valid"]]
    samples = [value for run in eligible for value in run["responses_s"].values()]
    emergency_count = sum(run["emergency_count"] for run in eligible)
    within = sum(run["within_target"] for run in eligible)
    completed = sum(group["completed"] for run in eligible for group in run["by_priority"].values())
    mission = [
        run["mission"]["duration_s"] for run in eligible if run["mission"] and run["mission"]["arrival_s"] >= 0
    ]
    row = {
        **{key: runs[0][key] for key in ("suite", "scenario", "policy")},
        "runs": len(runs), "valid_runs": len(eligible),
        "emergencies": emergency_count,
        "unserved_emergencies": sum(run["emergencies_unserved"] for run in eligible),
        "within_target_pct": 100 * within / emergency_count if emergency_count else None,
        **{f"response_{key}": val for key, val in statistics(samples).items()},
        "mean_weighted_tardiness_s": _mean(run["weighted_tardiness_s"] for run in eligible),
        **{f"served_lateness_{key}": val for key, val in statistics(
            [value for run in eligible for value in run.get("lateness_samples_s", [])]
        ).items()},
        "completed_tasks": completed,
        "correct_recoveries": sum(run["recovery_correct"] is True for run in eligible) if runs[0]["scenario"] == "breakdown" else None,
        "mean_fleet_distance_m": _mean(run["fleet_distance_m"] for run in eligible),
        "mean_changed_assignments": _mean(run["changed_assignments"] for run in eligible),
        "mission_arrivals": len(mission), "mission_mean_s": _mean(mission),
        "mission_mean_waiting_s": _mean(
            run["mission"]["waiting_s"] for run in eligible if run["mission"] and run["mission"]["arrival_s"] >= 0
        ),
        "mean_civilian_time_loss_s": _mean(run["civilian_time_loss"]["mean_s"] for run in eligible),
        "mean_civilian_p90_loss_s": _mean(run["civilian_time_loss"]["p90_s"] for run in eligible),
        "civilian_unfinished": sum(run["civilian_unfinished"] for run in eligible),
        "preemptions": sum(run["preemptions"] for run in eligible),
        "priority_restored": sum(run["priority_restored"] for run in eligible),
    }
    for priority in ("critical", "high", "routine"):
        groups = [run["by_priority"][priority] for run in eligible]
        total_deadlines = sum(group["with_deadline"] for group in groups)
        row[f"unserved_{priority}"] = sum(group["unserved"] for group in groups)
        row[f"on_time_pct_{priority}"] = 100 * sum(group["on_time"] for group in groups) / total_deadlines if total_deadlines else None
    return row


def _pair_runs(by_key: dict, conditions):
    rows = []
    effects = []
    for suite, scenario in conditions:
        for reference, candidate, label in CONTRASTS:
            if suite == "traffic" and (reference, candidate) != ("B0", "A2"):
                continue
            matched = []
            for seed in sorted({key[2] for key in by_key if key[:2] == (suite, scenario)}):
                a, b = (by_key.get((suite, scenario, seed, p)) for p in (reference, candidate))
                if a is None or b is None:
                    continue
                if a["inputs_sha256"] != b["inputs_sha256"]:
                    raise ValueError(f"Inputs disagree for {suite}/{scenario}/{seed}")
                jointly_reached = sorted(set(a["responses_s"]) & set(b["responses_s"]))
                before = [a["responses_s"][call] for call in jointly_reached]
                after = [b["responses_s"][call] for call in jointly_reached]
                a_mission, b_mission = a["mission"], b["mission"]
                duration_a = a_mission["duration_s"] if a_mission and a_mission["arrival_s"] >= 0 else None
                duration_b = b_mission["duration_s"] if b_mission and b_mission["arrival_s"] >= 0 else None
                valid = a["valid"] and b["valid"]
                delta = mean(before) - mean(after) if before else None
                row = {
                    "suite": suite, "scenario": scenario, "seed": seed, "reference": reference, "candidate": candidate,
                    "valid_pair": valid, "inputs_sha256": a["inputs_sha256"], "jointly_reached": len(before),
                    "reference_response_s": _mean(before), "candidate_response_s": _mean(after),
                    "response_improvement_s": delta, "response_improvement_pct": _relative_change(_mean(before), _mean(after)),
                    "reference_unserved_emergencies": a["emergencies_unserved"],
                    "candidate_unserved_emergencies": b["emergencies_unserved"],
                    "weighted_tardiness_improvement_s": a["weighted_tardiness_s"] - b["weighted_tardiness_s"],
                    "mission_improvement_s": duration_a - duration_b if duration_a is not None and duration_b is not None else None,
                    "mission_improvement_pct": _relative_change(duration_a, duration_b),
                    "civilian_mean_improvement_s": (
                        a["civilian_time_loss"]["mean_s"] - b["civilian_time_loss"]["mean_s"]
                        if a["civilian_time_loss"]["mean_s"] is not None and b["civilian_time_loss"]["mean_s"] is not None else None
                    ),
                }
                rows.append(row)
                if valid:
                    matched.append((row, before, after))
            if not matched:
                continue
            all_before = [value for _, before, _ in matched for value in before]
            all_after = [value for _, _, after in matched for value in after]
            deltas = [row["response_improvement_s"] for row, _, _ in matched if row["response_improvement_s"] is not None]
            lo, hi = _bootstrap(deltas)
            effects.append({
                "suite": suite, "scenario": scenario, "reference": reference, "candidate": candidate, "label": label,
                "matched_seeds": len(matched), "matched_calls": len(all_before),
                "response_improvement_s": mean(all_before) - mean(all_after) if all_before else None,
                "response_improvement_pct": _relative_change(_mean(all_before), _mean(all_after)),
                "ci_low_s": lo, "ci_high_s": hi,
                "mission_improvement_s": _mean(row["mission_improvement_s"] for row, _, _ in matched),
                "civilian_mean_improvement_s": _mean(row["civilian_mean_improvement_s"] for row, _, _ in matched),
            })
    return rows, effects


def build_report(root: Path) -> None:
    runs = []
    for path in sorted((root / "runs").glob("*/*/*/*/metrics.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        manifest = root / "inputs" / run["suite"] / run["scenario"] / str(run["seed"]) / "manifest.json"
        if digest(manifest) != run["inputs_sha256"]:
            raise ValueError(f"Run does not match its frozen input manifest: {path}")
        runs.append(run)
    if not runs:
        raise ValueError(f"No run files in {root}")
    grouped = defaultdict(list)
    by_key = {}
    for run in runs:
        key = (run["suite"], run["scenario"], run["policy"])
        grouped[key].append(run)
        by_key[(run["suite"], run["scenario"], run["seed"], run["policy"])] = run
    conditions = sorted({key[:2] for key in grouped})
    summary = [_condition(grouped[key]) for key in sorted(grouped)]
    paired, effects = _pair_runs(by_key, conditions)
    write_csv(root / "summary.csv", summary)
    write_csv(root / "paired_differences.csv", paired, None if paired else ["suite", "scenario", "seed", "valid_pair"])
    write_csv(root / "effects.csv", effects, None if effects else ["suite", "scenario", "reference", "candidate"])
    document = {
        "label": "Synthetic SUMO simulation", "response_target_s": runs[0]["response_target_s"],
        "sumo_versions": sorted({run["sumo_version"] for run in runs}),
        "git_shas": sorted({run["git_sha"] for run in runs if run["git_sha"]}),
        "run_count": len(runs), "invalid_runs": [
            {key: run[key] for key in ("suite", "scenario", "policy", "seed", "failures")}
            for run in runs if not run["valid"]
        ],
        "response": [row for row in summary if row["emergencies"]],
        "traffic": [row for row in summary if row["suite"] == "traffic"],
        "effects": effects,
    }
    write_json(root / "benchmark.json", document)
    for path in sorted((root / "runs" / "logistics" / "breakdown").glob("*/*/replay.json")):
        metrics = json.loads(path.with_name("metrics.json").read_text(encoding="utf-8"))
        if metrics["valid"] and metrics["recovery_correct"]:
            render_replay(path, root / "replay.html")
            break
    _charts(root, summary)


def _charts(root: Path, summary: list[dict]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts = root / "charts"
    charts.mkdir(exist_ok=True)
    colors = {"B0": "#536781", "A1": "#137b74", "A2": "#c07731", "A3": "#694aa1"}
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fields = [
        ("response", "Emergency response", "Seconds from call to eligible pickup", "response_mean_s", "response_p90_s"),
        ("logistics-tardiness", "Logistics tardiness", "Weighted seconds per run", "mean_weighted_tardiness_s", None),
        ("logistics-unserved", "Unserved logistics tasks", "Total at cutoff across valid runs", "unserved_critical", None),
        ("traffic-ambulance", "Frozen urgent mission", "Seconds from SUMO departure", "mission_mean_s", "mission_mean_waiting_s"),
        ("traffic-civilian", "Civilian time loss", "Seconds per arrived civilian trip", "mean_civilian_time_loss_s", "mean_civilian_p90_loss_s"),
    ]
    for slug, label, ylabel, primary, secondary in fields:
        subset = [row for row in summary if row["valid_runs"] and row[primary] is not None
                  and not (slug.startswith("logistics") and row["suite"] != "logistics")]
        if not subset:
            continue
        scenarios = sorted({row["scenario"] for row in subset})
        fig, axes = plt.subplots(1, len(scenarios), figsize=(max(5.5, len(scenarios) * 3.5), 3.8), squeeze=False)
        fig.suptitle(f"{label}  ·  Synthetic SUMO simulation", fontweight="bold", y=.99)
        for ax, scenario in zip(axes[0], scenarios):
            rows = sorted((row for row in subset if row["scenario"] == scenario), key=lambda r: POLICIES.index(r["policy"]))
            labels = [row["policy"] for row in rows]
            values = [row[primary] for row in rows]
            if slug == "logistics-unserved":
                bottom = [0] * len(rows)
                for priority, color in zip(("critical", "high", "routine"), ("#9f3e47", "#c87940", "#536781")):
                    group = [row[f"unserved_{priority}"] for row in rows]
                    ax.bar(labels, group, bottom=bottom, label=priority.title(), color=color, width=.55)
                    bottom = [a + b for a, b in zip(bottom, group)]
                ax.legend(frameon=False, fontsize=8)
            else:
                bars = ax.bar(labels, values, color=[colors[policy] for policy in labels], width=.55)
                ax.bar_label(bars, fmt="%.1f", padding=2, fontsize=8)
                if secondary:
                    name = "Mean waiting" if slug == "traffic-ambulance" else "P90" if slug == "response" else "Mean run P90"
                    ax.scatter(labels, [row[secondary] for row in rows], label=name, zorder=3, color="#202735", marker="D", s=20)
                    ax.legend(frameon=False, fontsize=8)
            ax.set_title(scenario.replace("-", " ").title())
            ax.set_ylabel(ylabel)
            ax.grid(axis="y", alpha=.22)
            ax.set_axisbelow(True)
            ax.margins(y=.18)
        fig.tight_layout()
        fig.savefig(charts / f"{slug}.png", dpi=170, bbox_inches="tight")
        plt.close(fig)
