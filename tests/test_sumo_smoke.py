"""Real SUMO smoke test -- skipped (with a reason) if SUMO is not installed
or the network hasn't been generated yet. Never asserts success without an
actual `sumo` process having run."""

from __future__ import annotations

from pathlib import Path

import pytest

CFG_PATH = Path("simulation/network/city.sumocfg")


def _sumo_available() -> bool:
    from rescueflow.sumo.env import resolve_sumo_env

    return resolve_sumo_env() is not None


pytestmark = pytest.mark.skipif(not _sumo_available(), reason="SUMO not found on this machine")


@pytest.mark.skipif(not CFG_PATH.exists(), reason="simulation/network/city.sumocfg not generated; run 'rescueflow sumo setup'")
def test_smoke_ambulance_moves_and_arrives():
    from rescueflow.sumo.controller import TraciSimulationController

    controller = TraciSimulationController(str(CFG_PATH))
    result = controller.run_smoke(duration_s=120)

    assert result.sumo_version and result.sumo_version != "unknown"
    assert result.ok, result.detail
