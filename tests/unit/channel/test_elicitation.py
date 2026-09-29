"""elicitation 三态映射 + elicit 通道单元测试（P4-4，红线 1 扁平 schema）。"""
from dataclasses import dataclass

import pytest

from decide_agent.app.entry import DecideApp, build_app
from decide_agent.channel.mcp_adapter.server import (
    ElicitAnswer,
    make_decision_impl,
    map_elicitation,
)

WEIGHTS = {
    "taste_match": 0.35, "distance": 0.2, "price": 0.2, "queue": 0.15, "weather_fit": 0.1,
}


def decide(tmp_path) -> DecideApp:
    return build_app(data_dir=tmp_path, decision_mode="experience")  # collector 已装配


def test_map_elicitation_accept():
    mode, extra = map_elicitation(None, "accept", {"answer": "辣"})
    assert mode == "respond" and extra == {"value": "辣"}


def test_map_elicitation_decline_and_cancel_fallback():
    for action in ("decline", "cancel", "", None):
        mode, extra = map_elicitation(None, action, {})
        assert mode == "fallback"
        assert "降级顺延" in extra["caveat"]


def test_elicit_schema_is_flat_primitives():
    """红线 1：扁平 schema，仅原始类型单字段。"""
    assert set(ElicitAnswer.model_fields) == {"answer"}
    assert ElicitAnswer.model_fields["answer"].annotation is str


@dataclass
class FakeResult:
    action: str
    data: object


class FakeContext:
    def __init__(self, action: str, answer: str = "辣") -> None:
        self.action = action
        self.answer = answer
        self.messages: list[str] = []

    async def elicit(self, message, schema):
        self.messages.append(message)
        return FakeResult(self.action, schema(answer=self.answer) if self.action == "accept" else None)


@pytest.mark.asyncio
async def test_elicit_accept_answers_inline(tmp_path):
    """accept → 内联续答 completed（一轮对话无需宿主 continuation）。"""
    app = decide(tmp_path)
    ctx = FakeContext("accept")
    outcome = await make_decision_impl(app, {
        "question": "我想吃饭", "interactive": True,
        "weights": WEIGHTS, "elicit_answers": True,
    }, ctx)
    assert outcome["status"] == "completed"
    assert any("吃辣" in m for m in ctx.messages)
    assert outcome["learned_memory"] == []  # 默认 scope=once 不落库（预期）


@pytest.mark.asyncio
async def test_elicit_decline_falls_back_to_continuation(tmp_path):
    """decline/cancel → 降级顺延：挂起保留 + caveat，宿主 continuation 兜底。"""
    app = decide(tmp_path)
    outcome = await make_decision_impl(app, {
        "question": "我想吃饭", "interactive": True,
        "weights": WEIGHTS, "elicit_answers": True,
    }, FakeContext("decline"))
    assert outcome["status"] == "require_action"
    assert outcome["pending"]["payload"]["dimension"] == "taste_match"
    assert "降级顺延" in outcome["pending"]["payload"].get("caveat", "")

    # continuation 兜底照常可用
    final = await make_decision_impl(app, {
        "decision_id": outcome["decision_id"],
        "request_id": outcome["pending"]["request_id"],
        "response": "辣",
    })
    assert final["status"] == "completed"


@pytest.mark.asyncio
async def test_elicit_disabled_keeps_continuation_contract(tmp_path):
    """未声明能力（elicit_answers=False）→ 走 require_action（100% 兼容兜底）。"""
    app = decide(tmp_path)
    outcome = await make_decision_impl(app, {
        "question": "我想吃饭", "interactive": True, "weights": WEIGHTS,
    }, FakeContext("accept"))
    assert outcome["status"] == "require_action"
    assert "caveat" not in outcome["pending"]["payload"]


@pytest.mark.asyncio
async def test_mcp_candidates_user_options(tmp_path):
    """用户直供选项（字符串自动规范化）→ 通用模式出推荐。"""
    app = decide(tmp_path)
    outcome = await make_decision_impl(app, {
        "question": "周末去哪放松？",
        "candidates": ["咖啡馆", "书店", "公园"],
        "interactive": False,
    })
    assert outcome["status"] == "completed"
    names = {a["candidate"]["name"] for a in
             [outcome["result"]["recommendation"], *outcome["result"]["alternatives"]]}
    assert names <= {"咖啡馆", "书店", "公园"}
