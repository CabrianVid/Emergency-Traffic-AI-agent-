"""Optional source capture. Rendering uses the checked-in plates and tracks."""
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

VIDEO = Path(__file__).resolve().parents[1]
REPO = VIDEO.parent
WORK = VIDEO / ".capture"
DATA = VIDEO / "data"
sys.path.insert(0, str(REPO / "src"))
os.environ.pop("OPENAI_API_KEY", None)
wheel_home = Path(sys.prefix) / "Lib/site-packages/sumo"
if (wheel_home / "bin/sumo.exe").exists():
    os.environ["SUMO_HOME"] = str(wheel_home)

from rescueflow.benchmark.montecarlo import Run, prepare
from rescueflow.dashboard.agent import DispatcherAgent
from rescueflow.dashboard.network import network_geometry
from rescueflow.dashboard.service import DispatchRun
from rescueflow.decision.agent import DecisionAgent


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def city():
    source = REPO / "simulation/presentation/ljubljana-wide-roads.osm.gz"
    target = WORK / "presentation" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    run = DispatchRun(WORK, DispatcherAgent())
    try:
        state = run.load("ljubljana", "urgent")
        save(WORK / "city.json", state)
        save(WORK / "city-geometry.json", run.geometry)
    finally:
        run.close()


class ArchiveAgent(DecisionAgent):
    """Read the original model result keyed by the actual request fingerprint."""
    def __init__(self, archive):
        super().__init__(enabled=False)
        self.archive = archive
        self.capture = None

    def _cache_get(self, fingerprint):
        name = f"ai-cache/{fingerprint[:2]}/{fingerprint}.json"
        return json.loads(self.archive.read(name)) if name in self.archive.NameToInfo else None

    def rank(self, world, event, options):
        result = super().rank(world, event, options)
        if result.is_ai and event and event.type.value == "new_urgent_task" and self.capture:
            self.capture(world, event, options, result)
        return result


def replay(seed, policy, archive):
    folder = prepare(WORK / "grid", seed)
    output = WORK / f"run-{seed}-{policy}"
    output.mkdir(exist_ok=True)
    record = json.loads(archive.read(f"runs/{seed:04d}/{policy}/metrics.json"))
    agent = ArchiveAgent(archive)
    run = Run(folder, output, policy, agent, engine=record.get("engine", 1))
    geometry = network_geometry(run.travel, "grid")
    samples = []
    chosen = {}

    def capture_decision(world, event, options, rank):
        if chosen:
            return
        proposals = []
        for key in rank.ranking:
            option = next(o for o in options if o.option_id == key)
            p = option.proposal
            proposals.append({
                **p.model_dump(mode="json"), **DispatcherAgent.describe_plan(world, p),
                **option.summary(), "recommended": key == rank.recommended,
                "rank": len(proposals) + 1, "ai_probability": rank.probabilities[key],
                "routes": run.controller.preview_routes(world, {v.vehicle_id: v.stops for v in p.vehicle_plans}),
            })
        t = world.sim_time_s
        state = json.loads((WORK / "city.json").read_text(encoding="utf-8"))
        state.update(world=world.model_dump(mode="json"), network="grid", run_id="archived-grid",
                     telemetry=run.controller.live_telemetry(world), proposals=proposals,
                     decision_mode=f"OpenAI Decisions · {rank.model}", before=None,
                     recommendation={**rank.as_dict(), "at_revision": world.plan_revision},
                     activity=[{"kind": event.type.value, "sim_time_s": event.sim_time_s,
                                "impacted_tasks": [event.payload["task_id"]]},
                               {"kind": "proposals_computed", "sim_time_s": t,
                                "message": f"{len(options)} feasible option(s), AI ranked"}])
        chosen.update(time=t, task=event.payload["task_id"], seed=seed,
                      fingerprint=rank.fingerprint, probabilities=rank.probabilities)
        save(WORK / "decision.json", state)
        save(WORK / "grid-geometry.json", geometry)

    if policy == "A1":
        agent.capture = capture_decision

    def sample(current):
        t = int(current.world.sim_time_s)
        if t <= 600 or (chosen and chosen["time"] <= t <= chosen["time"] + 130):
            obs = current.controller.live_telemetry(current.world)
            samples.append({"t": t, "fleet": {k: [round(x, 1) for x in v["xy"]]
                                              for k, v in obs["fleet"].items()},
                            "traffic": {k: [round(x, 1) for x in v] for k, v in obs.get("traffic", {}).items()
                                        if sum(k.encode()) % 3 == 0}})

    run.on_step = sample
    try:
        run.execute()
    finally:
        run.controller.close()
    observed = run.metrics(output, [])
    assert observed["calls"] == record["calls"], f"Replay mismatch: {seed} {policy}"
    if policy == "A1":
        assert chosen, "No cached multi-option call captured"
        state = json.loads((WORK / "decision.json").read_text(encoding="utf-8"))
        chosen["proposals"] = [{k: p[k] for k in ("strategy", "option_id", "routes", "metrics", "assignments")}
                               for p in state["proposals"]]
        chosen["callNode"] = state["world"]["tasks"][chosen["task"]]["pickup_node"]
        chosen["geometry"] = geometry
        chosen["committedRoutes"] = {k: v["route"] for k, v in state["telemetry"]["fleet"].items()}
        chosen["frames"] = [s for s in samples if chosen["time"] <= s["t"] <= chosen["time"] + 100]
        save(DATA / "decision.json", chosen)
        print("Decision:", chosen["time"], chosen["task"], [p["strategy"] for p in chosen["proposals"]])
    start = 160 + seed % 9 * 13
    return {"seed": seed, "policy": policy, "density": run.manifest["density"],
            "frames": [s for s in samples if start <= s["t"] < start + 120]}


if __name__ == "__main__":
    if "grid-only" not in sys.argv:
        city()
    if "city-only" not in sys.argv:
        archive_path = REPO / "experiments/published/montecarlo-2026-11-22/replay-data.zip"
        with zipfile.ZipFile(archive_path) as archive:
            tiles = [replay(seed, policy, archive) for seed, policy in
                     [(0, "A1"), (1, "B1"), (2, "R1"), (3, "B0"), (4, "A2"), (5, "B1")]]
        save(DATA / "tiles.json", tiles)
        print("Six replays matched the archived call-by-call responses.")
