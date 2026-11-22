"""Matched Monte Carlo inputs and the AI/fallback accounting."""

from types import SimpleNamespace

import pytest

from rescueflow.benchmark.montecarlo import RESPONSE_CAP_S, Run, FallbackUsed, scenario_score
from rescueflow.benchmark.scenarios import prepare
from rescueflow.decision.agent import SOURCE_AI, SOURCE_FALLBACK, DecisionAgent
from rescueflow.decision.alternatives import Alternative
from rescueflow.models.core import WorldState
from rescueflow.sumo.env import resolve_sumo_env

needs_sumo = pytest.mark.skipif(resolve_sumo_env() is None, reason="SUMO is required")


def _option(option_id, worst):
    metrics = {"worst_critical_response_eta_s": worst, "unserved_task_ids": [], "unserved_critical": 0,
               "estimated_deadline_misses": [], "reassigned_task_ids": [], "critical_tardiness_s": 0.0,
               "other_weighted_lateness_s": 0.0}
    return Alternative(option_id=option_id, strategy="urgent_first", proposal=None, metrics=metrics)


def _agent(tmp_path, create):
    agent = DecisionAgent(enabled=False, cache_dir=tmp_path, retries=0)
    agent.client = SimpleNamespace(decisions=SimpleNamespace(create=create))
    return agent


def test_ai_choice_ranks_by_probability_and_is_cached(tmp_path):
    calls = []

    def create(**request):
        calls.append(request)
        answer = SimpleNamespace(type="choice", choice="B", probabilities=[
            SimpleNamespace(value="A", probability=0.3), SimpleNamespace(value="B", probability=0.7)])
        return SimpleNamespace(answers=[answer], model="gpt-6-luna", usage=None)

    agent, options = _agent(tmp_path, create), [_option("A", 100), _option("B", 200)]
    first = agent.rank(WorldState(), None, options)
    second = agent.rank(WorldState(), None, options)
    assert first.source == SOURCE_AI and first.ranking == ["B", "A"] and first.deterministic_choice == "A"
    assert second.cache_hit and second.ranking == first.ranking and len(calls) == 1


@pytest.mark.parametrize("answer", [
    SimpleNamespace(type="refusal"), SimpleNamespace(type="choice", choice="Z", probabilities=[])])
def test_invalid_answers_become_labeled_uncached_fallback(tmp_path, answer):
    agent = _agent(tmp_path, lambda **_: SimpleNamespace(answers=[answer], model="m", usage=None))
    result = agent.rank(WorldState(), None, [_option("A", 300), _option("B", 100)])
    assert result.source == SOURCE_FALLBACK and result.ranking == ["B", "A"] and result.error
    assert not list(tmp_path.rglob("*.json"))


def test_unreached_calls_score_the_cap():
    assert scenario_score({"calls": {"a": {"capped_s": 100}, "b": {"capped_s": RESPONSE_CAP_S}}}) == (100 + RESPONSE_CAP_S) / 2


@needs_sumo
def test_policies_replay_identical_frozen_inputs(tmp_path):
    folder = prepare(tmp_path / "a", 3)
    again = prepare(tmp_path / "b", 3)
    assert (folder / "manifest.json").read_text() == (again / "manifest.json").read_text()
    runs = [Run(folder, tmp_path, policy, None) for policy in ("B0", "A1", "A2")]
    assert len({run.world.model_dump_json() for run in runs}) == 1
    assert all(run.manifest["events"] == runs[0].manifest["events"] for run in runs)
    (folder / "world.json").write_text("{}")
    with pytest.raises(ValueError, match="Input changed"):
        Run(folder, tmp_path, "B0", None)


@needs_sumo
def test_benchmark_refuses_fallback_decisions(tmp_path):
    agent = DecisionAgent(enabled=False)
    agent.rank = lambda world, event, options: SimpleNamespace(
        recommended=options[0].option_id, source=SOURCE_FALLBACK, model=None, latency_ms=None, cache_hit=False,
        error="down", deterministic_choice=None, probabilities={}, usage=None)
    run = Run(prepare(tmp_path, 1), tmp_path, "A1", agent)
    with pytest.raises(FallbackUsed):
        try:
            run.execute()
        finally:
            run.controller.close()
