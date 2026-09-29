"""Renderer tests: three formats (P1-6 acceptance), P0-golden text rendering."""
from decide_agent.core.narrator import OutputFormat, render
from decide_agent.schemas.candidate import Candidate
from decide_agent.schemas.decision import DecisionResult, DimensionScore, Recommendation
from decide_agent.schemas.workflow import (
    DecisionOutcome,
    PendingKind,
    PendingRequest,
)

DISPLAY = {"taste_match": "口味匹配", "distance": "距离"}


def completed_outcome() -> DecisionOutcome:
    rec = Recommendation(
        candidate=Candidate(id="a", name="蜀香居"),
        total_score=0.8258,
        dimension_scores=[
            DimensionScore(dimension="taste_match", score=0.9, weight=0.35, reason="命中口味"),
            DimensionScore(dimension="distance", score=0.875, weight=0.2),
        ],
        reason="综合得分 0.8258",
    )
    alt = Recommendation(
        candidate=Candidate(id="b", name="辣妹子炒菜"),
        total_score=0.79,
        dimension_scores=[DimensionScore(dimension="taste_match", score=0.9, weight=0.35)],
        reason="综合得分 0.79",
    )
    return DecisionOutcome(
        decision_id="d1", status="completed", state="suggested",
        result=DecisionResult(scene="food", recommendation=rec, alternatives=[alt], confidence=0.9),
        missing_information=["deals 不可用（neutral）"],
    )


def question_outcome() -> DecisionOutcome:
    pending = PendingRequest(
        request_id="r1", decision_id="d1", kind=PendingKind.QUESTION,
        payload={"dimension": "taste_match", "question": {
            "text": "今天想吃辣还是清淡？", "options": ["想吃辣", "清淡"], "allow_free_text": True,
        }},
        created_ts=0.0,
    )
    return DecisionOutcome(
        decision_id="d1", status="require_action", state="input-required", pending=pending,
    )


def test_json_minimal_strips_narrative():
    data = render(completed_outcome(), OutputFormat.JSON_MINIMAL)
    assert "reason" not in data["result"]["recommendation"]
    for alt in data["result"]["alternatives"]:
        assert "reason" not in alt
    assert data["result"]["recommendation"]["candidate"]["name"] == "蜀香居"  # 结构保留


def test_json_minimal_strips_question_text():
    data = render(question_outcome(), OutputFormat.JSON_MINIMAL)
    assert "question" not in data["pending"]["payload"]
    assert data["pending"]["payload"]["dimension"] == "taste_match"


def test_json_full_keeps_reason_and_adds_display_names():
    data = render(completed_outcome(), OutputFormat.JSON_FULL, display=DISPLAY)
    rec = data["result"]["recommendation"]
    assert rec["reason"] == "综合得分 0.8258"
    assert rec["dimension_scores"][0]["dimension_display"] == "口味匹配"
    assert data["missing_information"] == ["deals 不可用（neutral）"]


def test_json_full_restores_question_text():
    data = render(question_outcome(), OutputFormat.JSON_FULL, display=DISPLAY)
    assert data["pending"]["payload"]["question"]["text"] == "今天想吃辣还是清淡？"


def test_text_golden_completed():
    text = render(completed_outcome(), OutputFormat.TEXT, display=DISPLAY)
    lines = text.splitlines()
    assert lines[0] == "推荐：蜀香居（综合 0.83，非常匹配）"  # 档位解读
    assert "  · 口味匹配: 0.90 (权重 0.35)" in lines
    accept = next(ln for ln in lines if ln.startswith("可以直接说「接受」"))
    assert accept == "可以直接说「接受」，或补充信息（如预算、忌口）让结果更准。"
    assert any("综合得分 0~1" in ln for ln in lines)  # 刻度说明
    assert any("排第 1" in ln for ln in lines)  # 排名语境
    assert lines[-1] == "说明：deals 不可用（neutral）"


def test_text_question_and_empty_paths():
    asked = render(question_outcome(), OutputFormat.TEXT)
    assert asked.splitlines()[0] == "今天想吃辣还是清淡？"
    assert "其他（请输入）" in asked

    empty = DecisionOutcome(decision_id="d2", status="completed", state="suggested")
    assert render(empty, OutputFormat.TEXT).startswith("未能给出推荐")


def test_json_minimal_is_json_serialisable():
    import json

    for outcome in (completed_outcome(), question_outcome()):
        payload = render(outcome, OutputFormat.JSON_MINIMAL)
        json.dumps(payload, ensure_ascii=False)  # must not raise
