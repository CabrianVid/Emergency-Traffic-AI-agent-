"""The side-by-side scene may only compare runs of identical inputs."""

import json
from pathlib import Path

from rescueflow.sumo import presentation

ASSETS = Path(__file__).resolve().parents[1] / "simulation" / "presentation"


def test_stored_baseline_belongs_to_the_current_inputs():
    reference = presentation.load_reference(ASSETS)
    assert reference is not None, "inputs changed: run `presentation baseline --headless --save-reference`"
    assert set(reference["calls"]) == {call.id for call in presentation.CALLS}


def test_a_baseline_of_other_inputs_is_not_compared(tmp_path, monkeypatch):
    (tmp_path / "ljubljana-wide.net.xml.gz").write_bytes((ASSETS / "ljubljana-wide.net.xml.gz").read_bytes())
    (tmp_path / presentation.REFERENCE).write_text(json.dumps({"inputs": "other", "calls": {}}), encoding="utf-8")
    assert presentation.load_reference(tmp_path) is None


def test_difference_says_ahead_behind_faster_slower_and_same():
    view = presentation.CityView.__new__(presentation.CityView)
    view.reference = {"calls": {"C": {"response_s": 300.0, "unit": "R1"}}}
    view.reported, view.reached = {"C": 100.0}, {}
    assert view._difference("C", 200)[3] == 0
    assert view._difference("C", 450)[2] == "BEHIND by 00:50"
    view.reached = {"C": (220.0, "R2")}
    assert view._difference("C", 250) == ("waiting", "02:00", "AHEAD by 00:30", 1)
    assert view._difference("C", 500) == ("05:00", "02:00", "03:00 faster", 1)
    view.reached = {"C": (405.0, "R2")}
    assert view._difference("C", 500)[2:] == ("same", 0)
    view.reached = {"C": (480.0, "R2")}
    assert view._difference("C", 500)[2:] == ("01:20 slower", -1)
