"""Synthesis unit tests: P0-golden weighted merge, missing renormalisation, cross-validation."""
from decide_agent.core.decision.synthesis import cross_validate, synthesize, weights_with_missing
from decide_agent.schemas.decision import DimensionScore


def test_synthesize_p0_golden():
    scores = [
        DimensionScore(dimension="a", score=0.8, weight=0.6),
        DimensionScore(dimension="b", score=0.4, weight=0.4),
    ]
    assert synthesize(scores) == 0.64  # P0 test golden


def test_synthesize_missing_renormalises():
    scores = [
        DimensionScore(dimension="a", score=0.8, weight=0.6),
        DimensionScore(dimension="missing", score=0.5, weight=0.0),  # zeroed weight
    ]
    assert synthesize(scores) == 0.8  # renormalised, missing contributes nothing
    assert synthesize([]) == 0.0
    assert synthesize([DimensionScore(dimension="x", score=0.9, weight=0.0)]) == 0.0


def test_weights_with_missing():
    assert weights_with_missing({"a": 0.5, "b": 0.5}, ["b"]) == {"a": 0.5, "b": 0.0}


def test_cross_validate_agree_boost():
    confidence, ruled = cross_validate("A", "A", 0.80)
    assert confidence == 0.85 and ruled == "A"


def test_cross_validate_disagree_cut_and_choice_rules():
    confidence, ruled = cross_validate("A", "B", 0.80)
    assert confidence == 0.65 and ruled == "B"  # choice 裁决


def test_cross_validate_no_choice_is_passthrough():
    assert cross_validate("A", None, 0.7) == (0.7, "A")
    assert cross_validate(None, "A", 0.7) == (0.7, None)
