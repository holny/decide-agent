"""P4 transport tests: SystemOne / LLM via httpx.MockTransport（无真实密钥）。"""
import json

import httpx
import pytest

from decide_agent.models.providers.llm_provider import LLMProvider
from decide_agent.models.providers.systemone_adapter import SystemOneAdapter
from decide_agent.schemas.question import QuestionShape, TypedQuestion


def score_question() -> TypedQuestion:
    return TypedQuestion(shape="score", scene="food", dimension="taste_match",
                         candidate="蜀香居",
                         context={"candidate": {"id": "poi_000", "name": "蜀香居", "tags": ["辣"]},
                                  "slots": {"taste_match": "辣"}})


# ------------------------------------------------------------------ SystemOne

def test_systemone_probe_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    adapter = SystemOneAdapter(endpoint="http://x", model="jev-test", api_key_env="TYPESAFE_API_KEY")
    assert adapter.probe().availability == "unavailable"


def test_systemone_probe_available_with_key(monkeypatch):
    monkeypatch.setenv("TEST_SK", "k")
    adapter = SystemOneAdapter(endpoint="http://x", model="jev-test", api_key_env="TEST_SK")
    assert adapter.probe().availability == "available"


def test_systemone_answer_parses_official_contract(monkeypatch):
    monkeypatch.setenv("TEST_SK", "k")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        # 官方契约：ScoreAnswer.score = 期望等级（0-indexed）；5 档 rubric → 4.0/4 = 1.0
        return httpx.Response(200, json={
            "model": "jev-latest",
            "answers": {"q0": {"type": "score", "score": 4.0, "confidence": 0.82,
                               "legend": {"0": "非常不匹配", "4": "非常匹配"}}},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })

    adapter = SystemOneAdapter(
        endpoint="http://mock", api_key_env="TEST_SK", model="jev-latest",
        transport=httpx.MockTransport(handler),
    )
    answer = adapter.answer(score_question())
    assert answer.value == 1.0  # score 4.0 / levels 4 → 归一化
    assert answer.confidence == 0.82
    assert answer.provider == "decision_model"
    assert captured["url"].endswith("/v1/systemone")
    assert captured["auth"] == "Bearer k"
    body = captured["body"]
    assert body["model"] == "jev-latest"
    assert body["questions"]["q0"]["type"] == "score"
    assert len(body["questions"]["q0"]["criteria"]) == 5  # 官方 rubric 等级
    assert body["state"] == "food"  # 无 text 时 state 回落场景名
    instructions = body["questions"]["q0"]["instructions"]
    assert "蜀香居" in instructions  # 候选细节在 instructions


def test_systemone_choice_parse(monkeypatch):
    monkeypatch.setenv("TEST_SK", "k")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "jev-latest",
            "answers": {"q0": {"type": "choice", "choice": "food", "confidence": 0.94,
                               "probabilities": {"food": 0.94, "general": 0.06}}},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })

    adapter = SystemOneAdapter(endpoint="http://mock", model="jev-test", api_key_env="TEST_SK",
                               transport=httpx.MockTransport(handler))
    q = TypedQuestion(shape="classify", text="我想吃饭",
                      context={"scenes": ["food", "general"]})
    answer = adapter.answer(q)
    assert answer.value == "food"
    assert answer.distribution == {"food": 0.94, "general": 0.06}
    assert answer.confidence == 0.94


def test_systemone_http_error_raises_for_chain(monkeypatch):
    monkeypatch.setenv("TEST_SK", "k")
    adapter = SystemOneAdapter(
        endpoint="http://mock", model="jev-test", api_key_env="TEST_SK",
        transport=httpx.MockTransport(lambda request: httpx.Response(400)),
    )
    with pytest.raises(RuntimeError, match="systemone transport failed"):
        adapter.answer(score_question())  # 链捕获后降级（L4）


def test_systemone_missing_confidence_rejected(monkeypatch):
    monkeypatch.setenv("TEST_SK", "k")
    adapter = SystemOneAdapter(
        endpoint="http://mock", model="jev-test", api_key_env="TEST_SK",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"answers": {"q0": {"type": "score", "score": 2.0}}}))
    )
    with pytest.raises(RuntimeError, match="confidence"):
        adapter.answer(score_question())


# ------------------------------------------------------------------ LLM

def test_llm_probe_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = LLMProvider(base_url="http://mock/v1", model="m1", api_key_env="LLM_TEST_MISSING")
    assert provider.probe().availability == "unavailable"


def test_llm_answer_parses_json_content(monkeypatch):
    monkeypatch.setenv("TEST_LLM", "k")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        content = json.dumps({"value": 0.85, "confidence": 0.7, "basis": "辣味命中"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
        })

    provider = LLMProvider(
        base_url="http://mock/v1", model="test-model", api_key_env="TEST_LLM",
        transport=httpx.MockTransport(handler),
    )
    answer = provider.answer(score_question())
    assert answer.value == 0.85 and answer.confidence == 0.7
    assert answer.provider == "llm"


def test_llm_http_error_raises_for_chain():
    provider = LLMProvider(
        base_url="http://mock/v1", model="test-model",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with pytest.raises(RuntimeError, match="llm transport failed"):
        provider.answer(score_question())


def test_llm_classify_prompt_shape():
    assert "意图分类" in __import__(
        "decide_agent.models.providers.llm_provider",
        fromlist=["_SYSTEM_PROMPTS"])._SYSTEM_PROMPTS[QuestionShape.CLASSIFY.value]
