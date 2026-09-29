"""Fingerprint tests: skip on identical inputs, recompute on change, key independence."""
from decide_agent.core.workflow.fingerprint import StageFingerprint

EVIDENCE = {"candidates": [{"id": "a", "distance_m": 500}], "env": {"weather": "小雨"}}


def test_first_call_is_not_a_skip():
    fps = StageFingerprint()
    assert fps.should_skip("score", EVIDENCE) is False
    fps.update("score", EVIDENCE)
    assert fps.should_skip("score", EVIDENCE) is True  # identical -> Skip 复用
    assert fps.hits == ["score"]


def test_changed_payload_recomputes():
    fps = StageFingerprint()
    fps.update("score", EVIDENCE)
    changed = {"candidates": [{"id": "a", "distance_m": 900}], "env": {"weather": "小雨"}}
    assert fps.should_skip("score", changed) is False  # 变化 -> 只重算该阶段


def test_key_order_irrelevant():
    fps = StageFingerprint()
    fps.update("score", {"a": 1, "b": {"x": 2, "y": 3}})
    assert fps.should_skip("score", {"b": {"y": 3, "x": 2}, "a": 1}) is True


def test_stages_are_independent():
    fps = StageFingerprint()
    fps.update("classify", {"q": "x"})
    assert fps.should_skip("score", {"q": "x"}) is False  # other stage untouched
    assert fps.should_skip("classify", {"q": "x"}) is True


def test_update_refreshes():
    fps = StageFingerprint()
    fps.update("score", {"v": 1})
    fps.update("score", {"v": 2})
    assert fps.should_skip("score", {"v": 1}) is False
    assert fps.should_skip("score", {"v": 2}) is True
