"""DecisionEngine tests: four packets over the chain.

Acceptance groundwork (P1 gate): chain serving purely by experience (adapters
without keys auto-skip), batch scoring, unsupported shapes degrade structured.
"""
from pathlib import Path

from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.models.providers.llm_provider import LLMProvider
from decide_agent.models.providers.systemone_adapter import SystemOneAdapter
from tests.fixtures.providers import ListSink

REPO_SKILLS = Path(__file__).resolve().parents[4] / "skills"

CAND = {"id": "蜀香居", "tags": ["辣"], "distance_m": 500, "price_per_person": 65, "wait_min": 10}


def engine(*chain_tiers) -> DecisionEngine:
    return DecisionEngine(ProviderChain(list(chain_tiers)))


def test_experience_serves_score_via_engine():
    e = engine(ExperienceProvider(search_dirs=[REPO_SKILLS]))
    result = e.score_one("food", CAND, "taste_match", slots={"taste_match": "辣"})
    assert result.ok and result.provider == "experience"
    assert float(result.answer.value) == 0.9


def test_adapters_without_key_auto_skip(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sink = ListSink()
    e = DecisionEngine(ProviderChain([
        SystemOneAdapter(endpoint="http://localhost:8000", model="jev-test", api_key_env="TYPESAFE_API_KEY"),
        LLMProvider(base_url="http://localhost:8000/v1", model="llm-test", api_key_env="OPENAI_API_KEY"),
        ExperienceProvider(search_dirs=[REPO_SKILLS]),
    ], sink=sink))
    result = e.score_one("food", CAND, "taste_match", slots={"taste_match": "辣"})
    assert result.ok and result.provider == "experience"
    assert result.skipped == ["decision_model", "llm"]  # 无 key 自动跳过（P1 gate）
    assert [(r.provider, r.outcome) for r in sink.records] == [
        ("decision_model", "skipped"), ("llm", "skipped"), ("experience", "ok"),
    ]


def test_score_batch_shapes():
    e = engine(ExperienceProvider(search_dirs=[REPO_SKILLS]))
    results = e.score_batch(
        "food", [CAND, {"id": "辣妹子", "tags": ["辣"], "distance_m": 900,
                         "price_per_person": 55, "wait_min": 20}],
        ["taste_match", "price"], slots={"taste_match": "辣"},
    )
    assert len(results) == 4
    assert all(r.ok for r in results.values())
    assert float(results[("蜀香居", "taste_match")].answer.value) == 0.9
    assert float(results[("辣妹子", "price")].answer.value) == 0.7


def test_classify_keyword_fallback_extract_verify_degrade():
    e = engine(ExperienceProvider(search_dirs=[REPO_SKILLS]))  # experience: classify 规则兜底 ✓
    classify = e.classify("我想吃饭", ["food"])
    assert classify.ok and classify.answer.value == "food"  # §10.4 规则兜底
    assert e.extract("food", "预算100", "budget").ok  # experience extract ✓
    assert not e.verify("food", "taboo", "不吃香菜").ok  # verify 仍未实现 → 降级


def test_key_probe_available_with_key(monkeypatch):
    monkeypatch.setenv("TEST_K", "x")
    adapter = SystemOneAdapter(endpoint="http://x", model="jev-test", api_key_env="TEST_K")
    assert adapter.probe().availability == "available"
