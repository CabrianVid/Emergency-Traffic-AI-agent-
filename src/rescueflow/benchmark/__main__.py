"""Benchmark command line. Invoke with python -m rescueflow.benchmark."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click

from rescueflow.benchmark.inputs import LOGISTICS, POLICIES, TRAFFIC, prepare, write_json
from rescueflow.benchmark.runner import execute


def seeds_from(value: str) -> list[int]:
    try:
        if ":" in value:
            start, end = (int(part) for part in value.split(":"))
            seeds = list(range(start, end + 1))
        else:
            seeds = list(dict.fromkeys(int(part) for part in value.split(",")))
    except ValueError as exc:
        raise click.BadParameter("use an inclusive range such as 0:9, or comma-separated integers") from exc
    if not seeds or min(seeds) < 0:
        raise click.BadParameter("at least one nonnegative seed is required")
    return seeds


def run_worker(item: tuple) -> tuple[str, bool]:
    inputs, output, policy, record = item
    output.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", "rescueflow.benchmark", "worker", str(inputs), str(output), policy]
    if record:
        args.append("--record")
    result_file = output / "metrics.json"
    failure_file = output / "worker_failure.json"
    result_file.unlink(missing_ok=True)
    failure_file.unlink(missing_ok=True)
    with (output / "process.log").open("w", encoding="utf-8") as stream:
        result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT)
    metrics = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {}
    label = f"{output.parent.parent.parent.name}/{output.parent.parent.name}/{output.parent.name}/{policy}"
    if result.returncode or not metrics.get("valid"):
        reason = "; ".join(metrics.get("failures", [])) or "worker failed; see process.log"
        if not metrics:
            manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
            write_json(failure_file, {
                **{key: manifest[key] for key in ("suite", "scenario", "seed")},
                "policy": policy, "failures": [reason], "exit_code": result.returncode,
            })
        return f"{label}: {reason}", False
    return label, True


@click.group()
def cli():
    """Run matched synthetic SUMO experiments and build their report."""


@cli.command()
@click.option("--suite", type=click.Choice(["logistics", "traffic", "all", "smoke"]), default="smoke")
@click.option("--seeds", default=None, help="Inclusive range or comma-separated seeds.")
@click.option("--policies", default=None, help="Comma-separated B0,A1,A2,A3. Traffic always isolates B0 vs A2.")
@click.option("--output", type=click.Path(path_type=Path), default=Path("experiments"))
@click.option("--jobs", type=click.IntRange(1, 16), default=4)
@click.option("--scenario", help="Run only the named condition.")
def run(suite, seeds, policies, output, jobs, scenario):
    """Freeze inputs, start independent workers and aggregate run files."""
    outputs = output.resolve()
    seed_list = seeds_from(seeds or ("0:2" if suite == "smoke" else "0:9"))
    requested = tuple(dict.fromkeys(p.strip().upper() for p in policies.split(","))) if policies else POLICIES
    if not requested or any(p not in POLICIES for p in requested):
        raise click.BadParameter("policies must come from B0,A1,A2,A3")
    suites = ("logistics", "traffic") if suite in ("all", "smoke") else (suite,)
    work = []
    for name in suites:
        conditions = LOGISTICS if name == "logistics" else TRAFFIC
        run_policies = requested if name == "logistics" else ("B0", "A2")
        for condition in conditions:
            if scenario and scenario != condition:
                continue
            for seed in seed_list:
                inputs = prepare(outputs, name, condition, seed)
                for policy in run_policies:
                    folder = outputs / "runs" / name / condition / str(seed) / policy
                    record = name == "logistics" and condition == "breakdown" and seed == seed_list[0] and policy == run_policies[-1]
                    work.append((inputs, folder, policy, record))
    if not work:
        raise click.BadParameter("scenario does not occur in the selected suite")
    click.echo(f"Running {len(work)} SUMO processes ({jobs} concurrent workers).")
    invalid = 0
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        for label, valid in executor.map(run_worker, work):
            invalid += not valid
            click.echo(f"{'OK' if valid else 'INVALID'} {label}")
    from rescueflow.benchmark.report import build_report

    build_report(outputs)
    click.echo(f"{len(work) - invalid}/{len(work)} valid runs. Outputs: {outputs}")
    if invalid:
        raise click.exceptions.Exit(2)


@cli.command()
@click.option("--output", type=click.Path(path_type=Path), default=Path("experiments"))
def report(output):
    """Regenerate summaries and plots from existing observations."""
    from rescueflow.benchmark.report import build_report

    build_report(output.resolve())


@cli.command(hidden=True)
@click.argument("inputs", type=click.Path(path_type=Path))
@click.argument("output", type=click.Path(path_type=Path))
@click.argument("policy", type=click.Choice(POLICIES))
@click.option("--record", is_flag=True)
def worker(inputs, output, policy, record):
    execute(inputs, output, policy, record)


if __name__ == "__main__":
    cli()
