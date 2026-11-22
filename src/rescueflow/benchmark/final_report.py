"""Build the paired benchmark tables and Seaborn figures from recorded SUMO runs."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from math import ceil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator, PercentFormatter

from rescueflow.benchmark.inputs import digest

MAPS = {"grid": "Training grid", "ljubljana": "Ljubljana"}
POLICIES = {
    "B0": "Nearest unit", "B1": "Central queue", "R1Q": "Rule-ranked", "A1Q": "AI-ranked", "A2Q": "AI + green wave",
}
PALETTE = dict(zip(POLICIES, ("#5676A2", "#8494AC", "#8D71AD", "#458D7D", "#D28450")))
BACKGROUND, PANEL, TEXT, MUTED = "#FBFCFF", "#EBEDF5", "#1D293C", "#586680"
GOOD, BAD = "#2C865E", "#BB5261"
CAP, TARGET = 900, 300


def read_runs(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    runs, calls = [], []
    verified_networks = {}
    for network in MAPS:
        directory = root / network
        for manifest in sorted(directory.glob("inputs/*/manifest.json")):
            packet = json.loads(manifest.read_text(encoding="utf-8"))
            seed = packet["seed"]
            net_file = packet.get("net_file", "grid.net.xml")
            if network not in verified_networks:
                verified_networks[network] = digest(directory / net_file)
            expected = packet["files"]
            inputs_match = verified_networks[network] == expected[net_file] and all(
                digest(manifest.parent / file) == fingerprint for file, fingerprint in expected.items()
                if file != net_file
            )
            for code in POLICIES:
                folder = directory / "runs" / manifest.parent.name / code
                file = folder / "metrics.json"
                entry = {"map": network, "seed": seed, "policy": code, "density": packet["density"]}
                if not file.is_file():
                    reason = "AI unavailable" if (folder / "ai-unavailable.txt").exists() else "No recorded result"
                    runs.append({**entry, "status": "missing", "reason": reason})
                    continue
                metrics = json.loads(file.read_text(encoding="utf-8"))
                source = metrics.get("decision_sources", {})
                call_ids = {e["payload"]["task_id"] for e in packet["events"] if e["type"] == "new_urgent_task"}
                reasons = list(metrics["failures"])
                if not inputs_match:
                    reasons.append("Frozen input hash mismatch")
                if metrics["seed"] != seed or metrics["policy"] != code or set(metrics["calls"]) != call_ids:
                    reasons.append("Run does not match frozen scenario or policy")
                if any("fallback" in name.lower() and count for name, count in source.items()):
                    reasons.append("AI fallback")
                if metrics.get("ambulance_collisions", 0):
                    reasons.append("Ambulance collision")
                valid = metrics["valid"] and not reasons
                result = dict(entry, **{
                    key: metrics.get(key) for key in (
                        "engine", "git_sha", "equipment_completed", "equipment_total", "reassignments",
                        "civilian_finished", "civilian_planned", "civilian_mean_time_loss_s",
                        "phases_shortened", "priority_requests", "priority_restored", "insertion_errors",
                        "ai_differs_from_rule", "decision_sources",
                    )
                })
                result.update(status="valid" if valid else "invalid", reason="; ".join(reasons))
                runs.append(result)
                for call_id, call in metrics["calls"].items():
                    calls.append({**entry, "call_id": call_id, "valid": valid, **call})
    return pd.DataFrame(runs), pd.DataFrame(calls)


def summarize(runs: pd.DataFrame, calls: pd.DataFrame):
    valid = calls[calls["valid"]]
    scores = valid.groupby(["map", "seed", "policy"], as_index=False)["capped_s"].mean()
    common_calls, common_runs, pairs, all_effects, density_effects = [], [], [], [], []
    for network, frame in scores.groupby("map", sort=False):
        values = frame.pivot(index="seed", columns="policy", values="capped_s")
        available = [key for key in POLICIES if key in values]
        complete = values.dropna(subset=available).index
        common_calls.append(valid[(valid["map"] == network) & valid["seed"].isin(complete)])
        common_runs.append(runs[(runs["map"] == network) & runs["seed"].isin(complete) & (runs["status"] == "valid")])
        contrasts = list(combinations(available, 2))
        for reference, candidate in contrasts:
            both = values[[reference, candidate]].dropna()
            left = runs[(runs["map"] == network) & (runs["policy"] == reference)].set_index("seed")
            right = runs[(runs["map"] == network) & (runs["policy"] == candidate)].set_index("seed")
            same_engine = left.loc[both.index, "engine"] == right.loc[both.index, "engine"]
            both = both.loc[same_engine]
            if both.empty:
                continue
            diff = both[reference] - both[candidate]
            mean_ref = both[reference].mean()
            saved = 100 * diff.mean() / mean_ref if mean_ref > 0 else np.nan
            lower, upper = np.nan, np.nan
            omission_min, omission_max = np.nan, np.nan
            if len(both) >= 20 and mean_ref > 0:
                ix = np.random.default_rng(20261122).integers(0, len(both), size=(10000, len(both)))
                sampled_base = both[reference].to_numpy()[ix].mean(axis=1)
                ratios = 100 * diff.to_numpy()[ix].mean(axis=1) / sampled_base
                lower, upper = np.quantile(ratios, [0.025, 0.975])
            if len(both) >= 2:
                denominator = both[reference].sum() - both[reference]
                omission = 100 * (diff.sum() - diff) / denominator.where(denominator > 0)
                omission_min, omission_max = omission.min(), omission.max()
            label = {"map": network, "reference": reference, "candidate": candidate}
            traffic = pd.concat([
                left.loc[both.index, "civilian_mean_time_loss_s"].rename("left"),
                right.loc[both.index, "civilian_mean_time_loss_s"].rename("right"),
            ], axis=1).dropna()
            traffic_saved = (
                100 * (1 - traffic["right"].mean() / traffic["left"].mean())
                if len(traffic) and traffic["left"].mean() > 0 else np.nan
            )
            pairs.append({
                **label, "pairs": len(both), "reference_mean_s": mean_ref,
                "candidate_mean_s": both[candidate].mean(), "saved_s": diff.mean(), "saved_pct": saved,
                "ci_low_pct": lower, "ci_high_pct": upper,
                "median_saved_s": diff.median(), "omit_one_low_pct": omission_min,
                "omit_one_high_pct": omission_max, "largest_loss_seed": int(diff.idxmin()),
                "largest_gain_seed": int(diff.idxmax()),
                "civilian_delay_saved_pct": traffic_saved,
                "wins": int((diff > 1).sum()), "ties": int((diff.abs() <= 1).sum()), "losses": int((diff < -1).sum()),
            })
            density = left.loc[both.index, "density"]
            for seed, delta in diff.items():
                all_effects.append({**label, "seed": seed, "saved_s": delta, "density": density[seed]})
            for level, subset in both.groupby(density):
                density_effects.append({
                    **label, "density": level, "pairs": len(subset),
                    "saved_pct": 100 * (1 - subset[candidate].mean() / subset[reference].mean()),
                })
    shared_calls, shared_runs = pd.concat(common_calls), pd.concat(common_runs)
    summary = []
    for (network, code), group in shared_calls.groupby(["map", "policy"], sort=False):
        reached = group["response_s"].dropna()
        subset = shared_runs[(shared_runs["map"] == network) & (shared_runs["policy"] == code)]
        critical = group[group["priority"] == "critical"]
        ranked = sum(sources.get("openai-decisions", 0) for sources in subset["decision_sources"])
        changed_rankings = subset["ai_differs_from_rule"].sum()
        summary.append({
            "map": network, "policy": code, "scenarios": group["seed"].nunique(), "calls": len(group),
            "reached": len(reached), "reached_pct": 100 * len(reached) / len(group),
            "target_pct": 100 * (group["response_s"] <= TARGET).mean(),
            "critical_target_pct": 100 * (critical["response_s"] <= TARGET).mean(),
            "mean_capped_s": group.groupby("seed")["capped_s"].mean().mean(),
            "median_reached_s": reached.median(),
            "p90_reached_s": reached.sort_values().iloc[ceil(.9 * len(reached)) - 1] if len(reached) else np.nan,
            "civilian_mean_time_loss_s": subset["civilian_mean_time_loss_s"].mean(),
            "civilian_finished_pct": 100 * subset["civilian_finished"].sum() / subset["civilian_planned"].sum(),
            "phases_shortened": int(subset["phases_shortened"].sum()),
            "reassignments": int(subset["reassignments"].sum()),
            "ai_rankings": ranked,
            "ai_override_pct": 100 * changed_rankings / ranked if ranked else np.nan,
        })
    return pd.DataFrame(summary), shared_calls, pd.DataFrame(pairs), pd.DataFrame(all_effects), pd.DataFrame(density_effects)


def style() -> None:
    sns.set_theme(style="darkgrid", context="notebook", rc={
        "figure.facecolor": BACKGROUND, "axes.facecolor": PANEL, "savefig.facecolor": BACKGROUND,
        "axes.edgecolor": PANEL, "axes.labelcolor": TEXT, "axes.titlecolor": TEXT,
        "text.color": TEXT, "xtick.color": MUTED, "ytick.color": MUTED,
        "grid.color": "white", "grid.alpha": 1.0, "font.family": "DejaVu Sans",
    })


def canvas(title: str, subtitle: str, *, height: float = 5.45, top: float = .70):
    figure, axes = plt.subplots(1, 2, figsize=(13.4, height))
    figure.subplots_adjust(left=.10, right=.958, bottom=.195, top=top, wspace=.30)
    figure.text(.057, .933, "RESCUEFLOW   /   MATCHED SUMO SIMULATIONS", color="#526485", size=9, weight="bold")
    figure.text(.055, .84, title, size=25, weight="bold")
    figure.text(.057, .768, subtitle, size=10, color=MUTED)
    return figure, axes


def save(figure, folder: Path, name: str, note: str) -> None:
    figure.text(.057, .065, note, size=9, color=MUTED)
    figure.savefig(folder / f"{name}.png", dpi=185)
    plt.close(figure)


def response_histogram(calls: pd.DataFrame, folder: Path) -> None:
    fig, axes = canvas("Response-time distribution",
                       "Percentage of calls in each one-minute bin, shown separately for each policy.",
                       height=5.95, top=.63)
    handles = [Line2D([0], [0], color=color, lw=3, label=key) for key, color in PALETTE.items()]
    fig.legend(handles=handles, loc="center", bbox_to_anchor=(.50, .693), ncol=5,
               frameon=False, handletextpad=.5, columnspacing=1.8)
    upper = 0.0
    for ax, (network, label) in zip(axes, MAPS.items()):
        selected = calls[calls["map"] == network].copy()
        selected["minutes"] = selected["capped_s"] / 60
        if selected.empty:
            continue
        options = dict(x="minutes", hue="policy", hue_order=list(POLICIES), palette=PALETTE,
                       bins=np.arange(16), stat="percent", common_norm=False, multiple="layer",
                       element="step", legend=False, ax=ax)
        sns.histplot(selected, **options, alpha=.10, linewidth=0)
        sns.histplot(selected, **options, fill=False, alpha=.78, linewidth=1.6)
        upper = max(upper, ax.get_ylim()[1])
        ax.axvline(5, color=MUTED, linewidth=1.1, linestyle="--")
        ax.set(xlim=(0, 15), xticks=[0, 3, 5, 9, 12, 15], xlabel="Response time (minutes)",
               ylabel="Share of calls")
        ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
        count = int(selected.groupby("policy").size().max())
        ax.set_title(f"{label}  ·  {count} calls per policy", loc="left", weight="bold", pad=10)
    for ax in axes:
        ax.set_ylim(0, upper)
    save(fig, folder, "01-response-time-distribution",
         "Dashed line marks the five-minute target. The final bin includes missed calls and responses capped at 15 minutes.")


def response_bars(summary: pd.DataFrame, folder: Path, *, target: bool) -> None:
    column = "target_pct" if target else "mean_capped_s"
    heading = "Calls reached within five minutes" if target else "Average ambulance response time"
    subtitle = ("Higher is better. Every reported call counts toward the target."
                if target else "Lower is better. The average gives equal weight to each scenario.")
    fig, axes = canvas(heading, subtitle)
    max_mean = float(summary[column].max())
    padding = 2.0 if target else max_mean * .027
    for ax, (network, label) in zip(axes, MAPS.items()):
        frame = summary[summary["map"] == network]
        order = [code for code in POLICIES if code in frame["policy"].values]
        if not order:
            continue
        sns.barplot(frame, x=column, y="policy", hue="policy", order=order, palette=PALETTE,
                    legend=False, edgecolor=BACKGROUND, linewidth=1.0, ax=ax, errorbar=None, width=.67)
        values = frame.set_index("policy")
        for y, code in enumerate(order):
            value = values.at[code, column]
            ax.text(value + padding, y, f"{value:.1f}%" if target else f"{value:.1f} s", va="center",
                    weight="bold", color=TEXT, size=10)
        ax.set(xlim=(0, 114 if target else 1.23 * max_mean), ylabel="",
               xlabel="Share of all calls" if target else "Mean response time (seconds)")
        if target:
            ax.set_xticks(np.arange(0, 101, 20))
            ax.xaxis.set_major_formatter(PercentFormatter(100, decimals=0))
        else:
            ax.xaxis.set_major_locator(MaxNLocator(6, integer=True))
        ax.set_title(f"{label}  ·  {int(frame['scenarios'].iloc[0])} matched scenarios", loc="left",
                     weight="bold", pad=10)
    filename = "03-five-minute-target" if target else "02-average-response-time"
    note = ("Both maps use synthetic incidents and demand. Cohorts are matched across all five policies within each map."
            if target else "Response times are capped at 15 minutes. Missed calls receive the full 15-minute score.")
    save(fig, folder, filename, note)


def paired_effects(pairs: pd.DataFrame, folder: Path) -> None:
    contrasts = list(combinations(POLICIES, 2))
    fig, axes = canvas("How much response time did each policy save?",
                       "Positive percentages mean faster response. Each comparison uses the same valid scenarios for both policies.",
                       height=8.5, top=.71)
    fig.subplots_adjust(bottom=.145, left=.11, wspace=.34)
    high = max(float(pairs[["saved_pct", "ci_low_pct", "ci_high_pct"]].abs().max().max()), 8)
    limit = high * 1.27
    for ax, (network, label) in zip(axes, MAPS.items()):
        rows = pairs[pairs["map"] == network].set_index(["reference", "candidate"])
        points = []
        for y, comparison in enumerate(contrasts):
            if comparison not in rows.index:
                continue
            row = rows.loc[comparison]
            x = row["saved_pct"]
            points.append({"x": x, "y": y, "direction": "faster" if x >= 0 else "slower"})
            if pd.notna(row["ci_low_pct"]):
                ax.hlines(y, row["ci_low_pct"], row["ci_high_pct"], color="#8494AC", lw=2.0)
            offset = 9 if -limit * .7 < x < limit * .6 or x < -limit * .85 else -9
            ax.annotate(f"{x:+.1f}%  ·  n={int(row['pairs'])}", (x, y), xytext=(offset, 1),
                        textcoords="offset points", va="center", ha="left" if offset > 0 else "right",
                        color=TEXT, size=8.5, weight="bold", zorder=4)
        if points:
            sns.scatterplot(pd.DataFrame(points), x="x", y="y", hue="direction",
                            palette={"faster": GOOD, "slower": BAD}, s=64, legend=False, ax=ax,
                            zorder=3, edgecolor=BACKGROUND)
        ax.axvline(0, color="#7886A2", lw=1.0, ls="--")
        ax.set(xlim=(-limit, limit), ylim=(len(contrasts) - .5, -.5),
               yticks=range(len(contrasts)), yticklabels=[f"{a} → {b}" for a, b in contrasts],
               xlabel="Response time saved", ylabel="")
        ax.xaxis.set_major_locator(MaxNLocator(7))
        ax.xaxis.set_major_formatter(PercentFormatter(100, decimals=0))
        ax.set_title(label, loc="left", weight="bold", pad=12)
    save(fig, folder, "04-paired-response-improvement",
         "Dots show the mean percentage change. Lines show 95% paired bootstrap intervals for comparisons with at least 20 valid pairs.")


def report_text(runs: pd.DataFrame, summary: pd.DataFrame, pairs: pd.DataFrame) -> str:
    lines = [
        "# RescueFlow · matched SUMO evaluation", "",
        "The grid uses synthetic roads. Ljubljana uses real OpenStreetMap road geometry with simulated signals,",
        "traffic, incidents and receiving locations. All comparisons apply to these simulations.", "",
        "A scenario is the unit of comparison. A missed call scores 900 seconds. Percent savings use the",
        "matched baseline mean as the denominator. Pair counts can differ across comparisons. Intervals",
        "are 95% paired bootstrap intervals and require at least 20 valid pairs. The headline averages use",
        "the common valid cohort. The input manifests and logs preserve failed runs.", "",
    ]
    city_count = runs.loc[runs["map"] == "ljubljana", "seed"].nunique()
    if city_count > 20:
        lines.extend([
            f"Ljubljana attempted {city_count} seeds. Reserve seeds were added in numerical order after",
            "ambulance collisions reduced the number of scenarios valid under every policy.", "",
        ])
    lines.extend(["## Results and audit", ""])
    for key, title in MAPS.items():
        lines.extend([f"### {title}", "", "| Policy | Valid / planned | Common scenarios | Mean response | Reached | Within 5 min | P90 reached |",
                      "|---|---:|---:|---:|---:|---:|---:|"])
        for policy, name in POLICIES.items():
            audit = runs[(runs["map"] == key) & (runs["policy"] == policy)]
            data = summary[(summary["map"] == key) & (summary["policy"] == policy)]
            count = f"{sum(audit['status'] == 'valid')}/{len(audit)}"
            if data.empty:
                lines.append(f"| {policy} · {name} | {count} | — | — | — | — | — |")
                continue
            r = data.iloc[0]
            lines.append(f"| {policy} · {name} | {count} | {r.scenarios} | {r.mean_capped_s:.1f}s | "
                         f"{r.reached_pct:.1f}% ({r.reached}/{r.calls}) | {r.target_pct:.1f}% | {r.p90_reached_s:.0f}s |")
        lines.extend(["", "| Reference → candidate | Valid pairs | Seconds saved | Percent saved | 95% interval | Faster / tie / slower |",
                      "|---|---:|---:|---:|---:|---:|"])
        for _, r in pairs[pairs["map"] == key].iterrows():
            ci = f"[{r.ci_low_pct:+.1f}%, {r.ci_high_pct:+.1f}%]" if pd.notna(r.ci_low_pct) else "—"
            lines.append(f"| {r.reference} → {r.candidate} | {r.pairs} | {r.saved_s:+.1f}s | "
                         f"{r.saved_pct:+.1f}% | {ci} | {r.wins}/{r.ties}/{r.losses} |")
        lines.append("")
    lines.extend([
        "## Figures and supporting data", "",
        "- [Response-time distributions](01-response-time-distribution.png)",
        "- [Average response times](02-average-response-time.png)",
        "- [Five-minute target](03-five-minute-target.png)",
        "- [Percentage improvement and confidence intervals](04-paired-response-improvement.png)", "",
        "CSV files contain the per-call observations, run validity and failure reasons, all pairwise",
        "comparisons, and contrasts by traffic density.", "",
        "## Operational effects", "",
        "| Map / policy | Critical calls within 5 min | Civilian trips finished | Civilian time loss | Signal phases shortened | AI picks different from rule |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for _, r in summary.iterrows():
        rank = f"{r.ai_override_pct:.1f}% of {r.ai_rankings}" if r.ai_rankings else "—"
        lines.append(f"| {MAPS[r['map']]} / {r.policy} | {r.critical_target_pct:.1f}% | "
                     f"{r.civilian_finished_pct:.1f}% | {r.civilian_mean_time_loss_s:.1f}s | "
                     f"{r.phases_shortened} | {rank} |")
    lines.extend(["", "## Sensitivity to any one scenario", "",
                  "These ranges show every possible single-scenario omission. The main results use the full valid paired sets.", "",
                  "| Map / comparison | Full result | Range across single omissions | Seed with smallest gain | Civilian delay saved |",
                  "|---|---:|---:|---:|---:|"])
    picked = pairs[(pairs["candidate"] == "A2Q") & pairs["reference"].isin(["B1", "A1Q"]) & (pairs["pairs"] > 1)]
    for _, r in picked.iterrows():
        lines.append(f"| {MAPS[r['map']]} / {r.reference} → {r.candidate} | {r.saved_pct:+.1f}% | "
                     f"{r.omit_one_low_pct:+.1f}% to {r.omit_one_high_pct:+.1f}% | "
                     f"{r.largest_loss_seed} | {r.civilian_delay_saved_pct:+.1f}% |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.root / "report"
    output.mkdir(parents=True, exist_ok=True)
    runs, calls = read_runs(args.root)
    summary, shared, pairs, effects, density = summarize(runs, calls)
    for title, table in (
        ("runs", runs), ("calls", calls), ("policy-summary", summary), ("paired-comparisons", pairs),
        ("scenario-differences", effects), ("by-density", density),
    ):
        table.to_csv(output / f"{title}.csv", index=False, float_format="%.5f")
    (output / "results.md").write_text(report_text(runs, summary, pairs), encoding="utf-8")
    style()
    response_histogram(shared, output)
    response_bars(summary, output, target=False)
    response_bars(summary, output, target=True)
    paired_effects(pairs, output)
    with ZipFile(output / "rescueflow-benchmark.zip", "w", compression=ZIP_DEFLATED) as bundle:
        for pattern in ("*.png", "*.csv", "results.md"):
            for artifact in sorted(output.glob(pattern)):
                bundle.write(artifact, artifact.name)


if __name__ == "__main__":
    main()
