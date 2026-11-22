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
from matplotlib.ticker import PercentFormatter

from rescueflow.benchmark.inputs import digest

MAPS = {"grid": "Training grid", "ljubljana": "Ljubljana"}
POLICIES = {
    "B0": "Nearest unit", "B1": "Central queue", "R1Q": "Rule-ranked", "A1Q": "AI-ranked", "A2Q": "AI + green wave",
}
PALETTE = dict(zip(POLICIES, ("#9AABB8", "#C4CEDC", "#A78BFA", "#4ECADC", "#FFB46A")))
BACKGROUND, PANEL, TEXT, MUTED = "#101922", "#162331", "#F4F6FB", "#ADBDCC"
GOOD, BAD, NEUTRAL = "#61D6AB", "#FF8994", "#526A80"
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
        "axes.edgecolor": PANEL, "axes.labelcolor": MUTED, "axes.titlecolor": TEXT,
        "text.color": TEXT, "xtick.color": MUTED, "ytick.color": MUTED,
        "grid.color": "#33475B", "grid.alpha": 0.55, "font.family": "DejaVu Sans",
    })


def canvas(title: str, subtitle: str, rows=1, columns=2, height=7.5):
    figure, axes = plt.subplots(rows, columns, figsize=(15, height), squeeze=False)
    figure.subplots_adjust(left=0.10, right=0.965, bottom=0.14, top=0.80, wspace=0.35, hspace=0.48)
    figure.text(.055, .954, "RESCUEFLOW  /  MATCHED SUMO BENCHMARK", color=PALETTE["A2Q"], weight="bold", size=10)
    figure.text(.055, .89, title, size=30, weight="bold")
    figure.text(.055, .84, subtitle, size=12, color=MUTED)
    return figure, axes


def save(figure, folder: Path, name: str, note: str) -> None:
    figure.text(.055, .045, note, size=9, color=MUTED)
    figure.savefig(folder / f"{name}.png", dpi=185)
    plt.close(figure)


def overview(summary: pd.DataFrame, folder: Path) -> None:
    fig, axes = canvas("How fast did ambulances arrive?",
                       "Response and five-minute reach across the same valid scenarios within each map.",
                       rows=2, height=10)
    fig.subplots_adjust(left=.17, wspace=.57)
    ceiling = float(summary["mean_capped_s"].max()) * 1.20
    for col, (network, title) in enumerate(MAPS.items()):
        frame = summary[summary["map"] == network]
        order = [p for p in POLICIES if p in frame["policy"].values]
        if not order:
            continue
        for row, column in enumerate(("mean_capped_s", "target_pct")):
            ax = axes[row, col]
            sns.barplot(frame, x=column, y="policy", hue="policy", order=order,
                        palette=PALETTE, legend=False, edgecolor=PANEL, ax=ax)
            lookup = frame.set_index("policy")
            for y, policy in enumerate(order):
                value = lookup.at[policy, column]
                ax.text(value + (ceiling * .025 if not row else 2), y, f"{value:.1f}" + ("%" if row else " s"),
                        va="center", weight="bold", size=10)
            ax.set(xlim=(0, 112 if row else ceiling), ylabel="", xlabel=(
                "Calls reached within five minutes" if row else "Mean capped response · seconds"
            ))
            ax.set_yticks(range(len(order)), [f"{p} · {POLICIES[p]}" for p in order], fontsize=10)
            if row:
                ax.xaxis.set_major_formatter(PercentFormatter(100))
            heading = f"{title}  /  {int(frame['scenarios'].iloc[0])} scenarios" if not row else "Five-minute target"
            ax.set_title(heading, loc="left", weight="bold", pad=12)
    save(fig, folder, "01-response-overview",
         "Synthetic calls and traffic. Mean gives equal weight to each scenario. Missed calls score 900 seconds.")


def gains(pairs: pd.DataFrame, folder: Path) -> None:
    contrast_order = [("B0", "B1"), ("B0", "R1Q"), ("B0", "A1Q"), ("B0", "A2Q"),
                      ("B1", "R1Q"), ("B1", "A1Q"), ("B1", "A2Q"), ("R1Q", "A1Q"), ("R1Q", "A2Q"), ("A1Q", "A2Q")]
    fig, axes = canvas("What did each upgrade add?",
                       "Paired change in average response time. Positive values mean faster response.",
                       height=9.5)
    point_max = max(float(pairs[["saved_pct", "ci_low_pct", "ci_high_pct"]].abs().max().max()), 8)
    limit = point_max * 1.33
    for col, (network, title) in enumerate(MAPS.items()):
        ax = axes[0, col]
        stats = pairs[pairs["map"] == network].set_index(["reference", "candidate"])
        plot = []
        for y, pair in enumerate(contrast_order):
            if pair not in stats.index:
                continue
            row = stats.loc[pair]
            plot.append({"y": y, "x": row["saved_pct"], "direction": "faster" if row["saved_pct"] >= 0 else "slower"})
            if pd.notna(row["ci_low_pct"]):
                ax.hlines(y, row["ci_low_pct"], row["ci_high_pct"], color=MUTED, lw=2, zorder=2)
            x = row["saved_pct"]
            right_side = (0 <= x < limit * .67) or x < -limit * .67
            ax.annotate(f"{x:+.1f}%  ·  n={int(row['pairs'])}", (x, y), xytext=(10 if right_side else -10, 0),
                        textcoords="offset points", va="center", ha="left" if right_side else "right",
                        color=TEXT, size=9, zorder=4)
        if plot:
            sns.scatterplot(pd.DataFrame(plot), x="x", y="y", hue="direction", palette={"faster": GOOD, "slower": BAD},
                            s=90, legend=False, ax=ax, zorder=3, edgecolor=PANEL)
        ax.axvline(0, color=MUTED, lw=1.1, alpha=.8)
        ax.set(ylim=(len(contrast_order) - .45, -.55), xlim=(-limit, limit),
               yticks=range(len(contrast_order)), yticklabels=[f"{a} → {b}" for a, b in contrast_order],
               xlabel="Response time saved", ylabel="")
        ax.xaxis.set_major_formatter(PercentFormatter(100))
        ax.set_title(title, loc="left", weight="bold", pad=12)
    save(fig, folder, "02-paired-effects",
         "95% paired bootstrap intervals shown for ≥20 valid pairs. Gains compare each policy with its named reference.")


def response_distribution(calls: pd.DataFrame, folder: Path) -> None:
    fig, axes = canvas("When did help arrive?",
                       "Each curve shows the percentage of calls actually reached as the response clock advances.")
    for col, (network, title) in enumerate(MAPS.items()):
        ax = axes[0, col]
        data = calls[calls["map"] == network].copy()
        if data.empty:
            continue
        # Unreached calls stay in the denominator but sit beyond the plot.
        data["minutes"] = data["response_s"].fillna(CAP + 3600) / 60
        sns.ecdfplot(data, x="minutes", hue="policy", hue_order=list(POLICIES), palette=PALETTE,
                     ax=ax, linewidth=2.4, legend=not col)
        ax.axvline(TARGET / 60, ls="--", lw=1, color=MUTED)
        ax.text(TARGET / 60 + .16, .07, "5 min target", color=MUTED, fontsize=9)
        ax.set(xlim=(0, CAP / 60), ylim=(0, 1.035), xlabel="Minutes since the call", ylabel="Share of all calls reached")
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.set_title(f"{title}  /  {int(data.groupby('policy').size().max())} calls per policy", loc="left", weight="bold")
    save(fig, folder, "03-response-curves",
         "Matched cohort across available policies. Curves remain below 100% when a call was missed or took more than 15 minutes.")


def tradeoffs(pairs: pd.DataFrame, summary: pd.DataFrame, folder: Path) -> None:
    fig, axes = canvas("Consistency and traffic trade-offs",
                       "Averages can hide uneven results. These views separate scenario outcomes and civilian delay.")
    frame = pairs[(((pairs["reference"] == "B1") & (pairs["candidate"].isin(["A1Q", "A2Q"]))) |
                  ((pairs["reference"] == "A1Q") & (pairs["candidate"] == "A2Q")))].reset_index(drop=True)
    ax = axes[0, 0]
    for index, row in frame.iterrows():
        start = 0
        for col, color in (("wins", GOOD), ("ties", NEUTRAL), ("losses", BAD)):
            percentage = 100 * row[col] / row["pairs"]
            ax.barh(index, percentage, left=start, color=color, height=.64)
            if percentage >= 9:
                ax.text(start + percentage / 2, index, f"{percentage:.0f}%", ha="center", va="center",
                        color=BACKGROUND if col != "ties" else TEXT, weight="bold", size=9)
            start += percentage
    labels = [f"{MAPS[r['map']]}  ·  {r['reference']} → {r['candidate']}" for _, r in frame.iterrows()]
    ax.set(xlim=(0, 100), yticks=range(len(labels)), yticklabels=labels, xlabel="Share of matched scenarios")
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(PercentFormatter(100))
    ax.set_title("Faster / within 1 s / slower", loc="left", weight="bold", pad=12)
    ax = axes[0, 1]
    actual = summary[summary["policy"].isin(["B1", "A1Q", "A2Q"])].copy()
    actual["network"] = actual["map"].map(MAPS)
    sns.barplot(actual, x="network", y="civilian_mean_time_loss_s", hue="policy", edgecolor=PANEL,
                palette=PALETTE, ax=ax, legend=True)
    for bars in ax.containers:
        ax.bar_label(bars, fmt="%.0f s", padding=4, fontsize=9, color=TEXT)
    ax.margins(y=.2)
    ax.set(xlabel="", ylabel="Mean SUMO time loss · seconds")
    ax.set_title("Civilian traffic delay", loc="left", weight="bold", pad=12)
    fig.subplots_adjust(left=.22, wspace=.42)
    save(fig, folder, "04-reliability-and-traffic",
         "Civilian time loss includes entered trips still moving at cutoff. Outcomes within one second are shown as ties.")


def traffic_regimes(density: pd.DataFrame, folder: Path) -> None:
    fig, axes = canvas("Does traffic density change the result?",
                       "Response time saved within light, moderate, and heavy traffic. Each cell gives the matched sample size.")
    fig.subplots_adjust(right=.9, wspace=.44)
    contrasts = [("B0", "A2Q"), ("B1", "A2Q"), ("R1Q", "A1Q"), ("A1Q", "A2Q")]
    labels = [f"{a} → {b}" for a, b in contrasts]
    levels = ["light", "moderate", "heavy"]
    maximum = max(float(density["saved_pct"].abs().max()), 10)
    colorbar = fig.add_axes((.922, .17, .012, .57))
    colors = sns.diverging_palette(9, 153, s=80, l=48, center="dark", as_cmap=True)
    for col, (network, title) in enumerate(MAPS.items()):
        center = axes[0, col]
        subset = density[density["map"] == network].set_index(["reference", "candidate", "density"])
        values = pd.DataFrame(np.nan, index=labels, columns=levels)
        annotations = pd.DataFrame("", index=labels, columns=levels)
        for (a, b), label in zip(contrasts, labels):
            for level in levels:
                if (a, b, level) not in subset.index:
                    continue
                row = subset.loc[(a, b, level)]
                values.loc[label, level] = row["saved_pct"]
                annotations.loc[label, level] = f"{row['saved_pct']:+.1f}%\nn={int(row['pairs'])}"
        sns.heatmap(values, annot=annotations, fmt="", cmap=colors, center=0, vmin=-maximum, vmax=maximum,
                    cbar=bool(col), cbar_ax=colorbar if col else None, linewidths=2, linecolor=BACKGROUND,
                    annot_kws={"color": TEXT, "weight": "bold", "size": 11}, ax=center)
        center.set_title(title, loc="left", weight="bold", pad=12)
        center.tick_params(axis="y", labelrotation=0)
        center.tick_params(axis="x", labelrotation=0)
    colorbar.yaxis.set_major_formatter(PercentFormatter(100))
    save(fig, folder, "05-traffic-regimes",
         "Percentages use the matched baseline within each traffic level. Small subgroups are descriptive.")


def report_text(runs: pd.DataFrame, summary: pd.DataFrame, pairs: pd.DataFrame) -> str:
    lines = [
        "# RescueFlow · matched SUMO evaluation", "",
        "The grid uses synthetic roads. Ljubljana uses real OpenStreetMap road geometry with simulated signals,",
        "traffic, incidents and receiving locations. All comparisons apply to these simulations.", "",
        "A scenario is the unit of comparison. A missed call scores 900 seconds. Percent savings use the",
        "matched baseline mean as the denominator. Pair counts can differ across comparisons. Intervals",
        "are 95% paired bootstrap intervals and require at least 20 valid pairs. The headline averages use",
        "the common valid cohort. The input manifests and logs preserve failed runs.", "",
        "## Results and audit", "",
    ]
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
        "- [Response and five-minute target](01-response-overview.png)",
        "- [Paired effects and confidence intervals](02-paired-effects.png)",
        "- [Share of calls reached over time](03-response-curves.png)",
        "- [Consistency and civilian traffic](04-reliability-and-traffic.png)",
        "- [Effects by traffic density](05-traffic-regimes.png)", "",
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
    overview(summary, output)
    gains(pairs, output)
    response_distribution(shared, output)
    tradeoffs(pairs, summary, output)
    traffic_regimes(density, output)
    with ZipFile(output / "rescueflow-benchmark.zip", "w", compression=ZIP_DEFLATED) as bundle:
        for pattern in ("*.png", "*.csv", "results.md"):
            for artifact in sorted(output.glob(pattern)):
                bundle.write(artifact, artifact.name)


if __name__ == "__main__":
    main()
