from pathlib import Path

import pytest

from rescueflow.benchmark.inputs import digest, frozen_departures, prepare
from rescueflow.benchmark.runner import audit_stop_warnings, percentile90
from rescueflow.sumo.env import resolve_sumo_env


def test_exogenous_departures_and_trip_percentile_are_deterministic():
    demand = frozen_departures(5, 240)
    assert demand == frozen_departures(5, 240)
    assert demand != frozen_departures(6, 240)
    assert demand == sorted(demand)
    assert percentile90([8, 3, 9, 1, 2, 6, 7, 4, 5, 10]) == 9


@pytest.mark.skipif(resolve_sumo_env() is None, reason="SUMO is required to generate the frozen grid")
def test_repreparing_seed_preserves_frozen_manifest(tmp_path: Path):
    inputs = prepare(tmp_path, "logistics", "closure", 5)
    manifest_hash = digest(inputs / "manifest.json")
    route_file = (inputs / "frozen.rou.xml").read_text(encoding="utf-8")
    assert " A4A5 " not in route_file
    assert prepare(tmp_path, "logistics", "closure", 5) == inputs
    assert digest(inputs / "manifest.json") == manifest_hash


def test_skip_warning_is_benign_only_if_trip_restarted_at_replacement():
    warning = "Warning: Vehicle 'R1' skips stop on lane 'A0A1_0' time=206.00."
    old = {"vehicle_id": "R1", "depart_s": 176, "arrival_s": 206, "vaporized": "traci"}
    new = {"vehicle_id": "R1", "depart_s": 206, "arrival_s": 1900, "vaporized": ""}
    assert audit_stop_warnings([warning], [old]) == (0, 1)
    assert audit_stop_warnings([warning], [old, new]) == (1, 0)
