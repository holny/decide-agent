"""Gating unit tests: P0-golden penalties + should_ask threshold."""
from decide_agent.core.decision.gating import evaluate


def test_no_missing_enough_candidates():
    verdict = evaluate([], 3, threshold=0.6)
    assert verdict.confidence == 1.0 and not verdict.should_ask


def test_missing_penalty_p0_golden():
    verdict = evaluate(["taste_match"], 3, threshold=0.6, penalty_per_missing=0.40)
    assert verdict.confidence == 0.6
    assert not verdict.should_ask  # exactly at threshold -> not below


def test_few_candidates_penalty():
    verdict = evaluate([], 1, threshold=0.95)
    assert verdict.confidence == 0.8  # 1.0 - 0.10*2
    assert verdict.should_ask


def test_floor_at_zero():
    verdict = evaluate(["a", "b", "c"], 0, threshold=0.1)
    assert verdict.confidence == 0.0 and verdict.should_ask
