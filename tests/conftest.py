from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NET_PATH = REPO_ROOT / "simulation" / "network" / "city.net.xml"


def sumo_available() -> bool:
    try:
        from rescueflow.sumo.env import resolve_sumo_env

        return resolve_sumo_env() is not None
    except ImportError:
        return False


@pytest.fixture(scope="session")
def net_path() -> str:
    if not NET_PATH.exists():
        pytest.skip(f"{NET_PATH} not generated; run 'rescueflow sumo setup' first")
    return str(NET_PATH)


@pytest.fixture(scope="session")
def travel(net_path):
    from rescueflow.travel import SumoTravelTimeProvider

    return SumoTravelTimeProvider(net_path)


@pytest.fixture
def world(travel):
    from rescueflow.scenario.loader import build_initial_world

    return build_initial_world(travel)
