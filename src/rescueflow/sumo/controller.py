"""The one process that owns a TraCI connection (docs/CONTRACTS.md
"Ownership and process layout"). No other module calls `traci` directly.

Prompt 1 implements a genuine smoke run (`run_smoke`): a real `sumo`
subprocess, a real ambulance vehicle moving on a real route, with its actual
arrival observed through TraCI -- never a hardcoded "it works" claim. The
full per-stop service/custody execution loop (`apply_committed_plan`,
continuous `step`) is scaffolding for stage 2 and is marked as such in
docs/IMPLEMENTATION_STATUS.md.
"""

from __future__ import annotations

import time

from rescueflow.interfaces.protocols import SmokeRunResult
from rescueflow.models.core import SimulationSnapshot, WorldState
from rescueflow.sumo.env import ensure_sumolib_importable, resolve_sumo_env


class TraciSimulationController:
    """Concrete `SimulationController`."""

    def __init__(self, sumocfg_path: str) -> None:
        self.sumocfg_path = sumocfg_path
        self._connected = False
        self._telemetry_seq = 0

    def load_scenario(self, world: WorldState) -> None:
        # Stage 2: map WorldState stops onto TraCI vehicle routes/stops.
        raise NotImplementedError("Per-task SUMO execution loop is planned for prompt 2.")

    def step(self, n: int = 1) -> SimulationSnapshot:
        import traci  # noqa: PLC0415

        for _ in range(n):
            traci.simulationStep()
        self._telemetry_seq += 1
        positions = {
            vid: {"edge": traci.vehicle.getRoadID(vid), "pos": traci.vehicle.getLanePosition(vid)}
            for vid in traci.vehicle.getIDList()
        }
        return SimulationSnapshot(
            telemetry_seq=self._telemetry_seq, sim_time_s=traci.simulation.getTime(), vehicle_positions=positions
        )

    def apply_committed_plan(self, world: WorldState) -> None:
        raise NotImplementedError("Per-task SUMO execution loop is planned for prompt 2.")

    def run_smoke(self, duration_s: int) -> SmokeRunResult:
        """Runs the frozen smoke scenario headless for `duration_s` simulated
        seconds (or until the smoke ambulance arrives, whichever is first)
        and reports whether it actually moved and arrived."""
        env = resolve_sumo_env()
        if env is None:
            return SmokeRunResult(
                sumo_version="unknown", duration_wall_ms=0.0, sim_time_elapsed_s=0.0,
                vehicles_arrived=0, ok=False, detail="SUMO not found; see docs/RUNBOOK.md",
            )
        ensure_sumolib_importable()
        import traci  # noqa: PLC0415

        sumo_version = self._read_sumo_version(env)
        wall_start = time.perf_counter()
        traci.start([str(env.sumo_binary), "-c", self.sumocfg_path, "--no-step-log", "--duration-log.disable"])
        self._connected = True
        try:
            arrived = 0
            observed_positions: list[tuple[str, float]] = []
            elapsed_s = 0.0
            step = 0
            while step < duration_s:
                traci.simulationStep()
                step += 1
                elapsed_s = traci.simulation.getTime()
                if "smoke_amb" in traci.vehicle.getIDList():
                    observed_positions.append((traci.vehicle.getRoadID("smoke_amb"), traci.vehicle.getLanePosition("smoke_amb")))
                arrived += traci.simulation.getArrivedNumber()
                if "smoke_amb" not in traci.vehicle.getIDList() and observed_positions:
                    break
            moved = len({p[0] for p in observed_positions}) > 1
            ambulance_arrived = len(observed_positions) > 0 and "smoke_amb" not in traci.vehicle.getIDList()
            wall_ms = (time.perf_counter() - wall_start) * 1000
            ok = moved and ambulance_arrived
            detail = (
                f"ambulance visited {len({p[0] for p in observed_positions})} distinct edges, "
                f"arrived={ambulance_arrived}, total arrivals (all vehicles)={arrived}"
            )
            return SmokeRunResult(
                sumo_version=sumo_version, duration_wall_ms=wall_ms, sim_time_elapsed_s=elapsed_s,
                vehicles_arrived=arrived, ok=ok, detail=detail,
            )
        finally:
            traci.close()
            self._connected = False

    def close(self) -> None:
        if self._connected:
            import traci  # noqa: PLC0415

            traci.close()
            self._connected = False

    @staticmethod
    def _read_sumo_version(env) -> str:
        import subprocess

        result = subprocess.run([str(env.sumo_binary), "--version"], capture_output=True, text=True, check=True)
        first_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
        return first_line.strip()
