"""Integration: experience provider through the real chain with the real recorder.

P1-2 acceptance proof: pure-rules full run (chain -> experience -> JSONL), and
L4 degrade when the scene has no rules (no crash, structured failure).
"""
from pathlib import Path

from decide_agent.core.decision.chain import ProviderChain
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.experience.recorder import JudgmentRecorder
from tests.fixtures.providers import score_question

REPO_SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _question(dimension="口味", candidate=None, slots=None):
    q = score_question()
    q.dimension = dimension
    q.context = {
        "candidate": candidate or {"tags": ["辣"], "distance_m": 500,
                                    "price_per_person": 65, "wait_min": 10},
        "env": {"weather": "小雨"},
        "slots": slots or {"taste_match": "辣"},
    }
    return q


def test_experience_full_run_through_chain(tmp_path):
    sink = JudgmentRecorder(base_dir=tmp_path)
    chain = ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])], sink=sink)
    result = chain.answer(_question(dimension="taste_match"))
    assert result.ok
    assert result.provider == "experience"
    assert float(result.answer.value) == 0.9  # 辣 + tags[辣] -> hit
    assert result.answer.confidence == 0.65

    records = sink.read_all()
    assert len(records) == 1
    assert records[0].provider == "experience"
    assert records[0].outcome == "ok"


def test_scene_without_rules_degrades_structured(tmp_path):
    sink = JudgmentRecorder(base_dir=tmp_path)
    chain = ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])], sink=sink)

    neutral = chain.answer(_question(dimension="ambiance"))  # unknown dimension -> neutral 0.5, not an error
    assert neutral.ok and float(neutral.answer.value) == 0.5

    from decide_agent.schemas.question import TypedQuestion
    missing = ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])], sink=sink)
    out = missing.answer(TypedQuestion(shape="score", scene="no_such_scene", dimension="价格"))
    assert not out.ok
    assert out.failed == ["experience"]
    assert "no rules.jsonc" in (out.errors[0].detail or "")
    assert sink.read_all()[-1].outcome == "failed"
