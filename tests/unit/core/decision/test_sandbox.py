"""Sandbox tests: P4-3 flip probability (independent unknowns, triangular closed form)."""
from decide_agent.core.decision.sandbox import flip_probabilities, potential_flip_dims


def _scores(a_x, b_x, w_x=0.5, w_y=0.5):
    """缺失维度以中性 0.5×w 占位（kernel 实际语义）。"""
    return {
        "A": [
            DimensionScore(dimension="x", score=a_x, weight=w_x),
            DimensionScore(dimension="y", score=0.5, weight=w_y),
        ],
        "B": [
            DimensionScore(dimension="x", score=b_x, weight=w_x),
            DimensionScore(dimension="y", score=0.5, weight=w_y),
        ],
    }


from decide_agent.schemas.decision import DimensionScore


def test_flip_partial_probability():
    # A x=0.9 (a=0.45), B x=0.6 (a=0.3), d=0.15, w=0.5 → p=(0.7)²/2≈0.245
    probs = dict(flip_probabilities(_scores(0.9, 0.6), {"x": 0.5, "y": 0.5}, ["y"]))
    assert probs["y"] == 0.245
    assert potential_flip_dims(_scores(0.9, 0.6), {"x": 0.5, "y": 0.5}, ["y"]) == ["y"]


def test_near_tie_caps_at_half():
    # 对称未知下 p 上限 0.5（d→0）
    probs = dict(flip_probabilities(_scores(1.0, 0.99), {"x": 0.5, "y": 0.5}, ["y"]))
    assert 0.45 < probs["y"] <= 0.5


def test_robust_when_gap_exceeds_weight():
    # d ≥ w → 未知值无法翻越 → 不问（L5）
    weights = {"x": 0.9, "y": 0.1}
    assert dict(flip_probabilities(_scores(1.0, 0.2, w_x=0.9, w_y=0.1), weights, ["y"])).get("y", 0.0) == 0.0
    assert potential_flip_dims(_scores(1.0, 0.2, w_x=0.9, w_y=0.1), weights, ["y"]) == []


def test_zero_weight_dimension_ignored():
    weights = {"x": 0.9, "y": 0.1}
    risky = potential_flip_dims(_scores(0.9, 0.6, w_x=0.9, w_y=0.1), weights, ["z", "y"])
    assert risky == []  # z 无权重永不问；y 间距 0.27 ≥ w=0.1 → 稳健


def test_sorted_by_probability_desc():
    weights = {"x": 0.2, "y": 0.5, "z": 0.3}
    ds = {
        "A": [DimensionScore(dimension="x", score=0.9, weight=0.2),
              DimensionScore(dimension="y", score=0.5, weight=0.5),
              DimensionScore(dimension="z", score=0.5, weight=0.3)],
        "B": [DimensionScore(dimension="x", score=0.2, weight=0.2),
              DimensionScore(dimension="y", score=0.5, weight=0.5),
              DimensionScore(dimension="z", score=0.5, weight=0.3)],
    }
    probs = flip_probabilities(ds, weights, ["y", "z"])
    assert probs[0][0] == "y"  # w 大者概率高
