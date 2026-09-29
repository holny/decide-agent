"""ContextEngine tests: per-stage view trimming (P1-7 acceptance)."""
import json

from decide_agent.core.context import ContextEngine, to_json
from decide_agent.schemas.candidate import Candidate
from decide_agent.schemas.collect import CollectTask, DegradePolicy
from decide_agent.schemas.decision import DecisionResult, DimensionScore, Recommendation


def test_intent_view_minimal():
    view = ContextEngine().for_intent("想吃辣", ["food", "general"], ["口味偏好：辣"])
    assert view["question"] == "想吃辣"
    assert view["scenes"] == ["food", "general"]
    assert view["memory_topics"] == ["口味偏好：辣"]
    assert "candidates" not in view and "scores" not in view and "history" not in view


def test_collect_view():
    tasks = [CollectTask(capability="location", fallback=DegradePolicy.ASK_ONCE)]
    view = ContextEngine().for_collect(tasks, resolved={"weather": "小雨"}, pending=[{"slot": "location"}])
    assert view["tasks"][0]["capability"] == "location"
    assert view["resolved"] == {"weather": "小雨"}
    assert view["pending"] == [{"slot": "location"}]


def test_decision_view_is_scoring_envelope():
    cand = {"id": "a", "tags": ["辣"], "distance_m": 500}
    view = ContextEngine().for_decision([cand], env={"weather": "小雨"}, slots={"taste_match": "辣"}, taboos=["香菜"])
    assert set(view) == {"candidate", "env", "slots", "taboos"}  # 结构化 state，无对话
    assert view["candidate"]["id"] == "a"
    assert view["taboos"] == ["香菜"]


def test_analysis_view_strips_raw_payloads():
    result = DecisionResult(
        scene="food",
        recommendation=Recommendation(
            candidate=Candidate(id="a", name="蜀香居", raw_data_ref="/raw/poi_000.json"),
            total_score=0.83,
            dimension_scores=[DimensionScore(dimension="taste_match", score=0.9, weight=0.35)],
            reason="x",
        ),
        confidence=0.9,
    )
    view = ContextEngine().for_analysis(result, style="简洁")
    assert view["raw_data_refs"] == ["/raw/poi_000.json"]  # 原始大 JSON 只留指针
    assert view["recommendation_summary"]["candidate"] == "蜀香居"
    assert view["recommendation_summary"]["top_factors"][0]["dimension"] == "taste_match"
    assert view["style"] == "简洁"
    json.loads(to_json(view))  # 可序列化
