"""Agent Loop 单测：工具执行器 + 脚本化规划器（零网络）。"""
import json
from pathlib import Path

import httpx
import pytest

from decide_agent.app.entry import DecideApp
from decide_agent.core.agent.dialogue import AgentToolkit, build_conversation_state
from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.schemas.workflow import DecisionRequest

REPO_SKILLS = Path(__file__).resolve().parents[4] / "skills"
SHU = {"id": "蜀香居", "name": "蜀香居", "tags": ["辣"], "distance_m": 500,
       "price_per_person": 65, "wait_min": 10}
LA = {"id": "辣妹子", "name": "辣妹子", "tags": ["辣"], "distance_m": 900,
      "price_per_person": 55, "wait_min": 20}
WEIGHTS = {"taste_match": 0.35, "distance": 0.2, "price": 0.2, "queue": 0.15, "weather_fit": 0.1}
ENV = {"weather": "小雨"}


def kernel() -> DecisionKernel:
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    return DecisionKernel(
        engine,
        weights_resolver=lambda s: WEIGHTS,
        gate_threshold=0.7,
        scenes=["food", "travel", "general", "chat"],
        question_resolver=lambda s, d: (
            {"text": "偏好口味？", "options": ["辣", "清淡"]} if d == "taste_match" else None),
    )


class ScriptedPlanner:
    """按脚本吐动作；脚本耗尽 → reply done（不抛错）。动作可为 callable(state, obs)。"""

    def __init__(self, actions: list) -> None:
        self.actions = list(actions)

    def plan(self, state: dict, user_input: str, observations: list[dict]) -> dict:
        if not self.actions:
            return {"action": "reply", "text": "done"}
        action = self.actions.pop(0)
        return action(state, observations) if callable(action) else action


def toolkit(generator=None) -> AgentToolkit:
    return AgentToolkit(kernel=kernel(), memory=None, experience_classify=None,
                        candidate_generator=generator)


def test_experience_baseline_tool_shape():
    classify = lambda text: {"scene": "food", "confidence": 0.6}
    tk = AgentToolkit(kernel=kernel(), memory=None, experience_classify=classify)
    material = tk.experience_baseline("想吃辣")
    assert material["keyword_prior"]["scene"] == "food"


def test_recall_memory_empty_without_memory():
    assert toolkit().recall_memory("辣") == []


def test_unknown_tool_reports_error():
    assert "error" in toolkit().execute("nope", {})


def test_start_decision_generates_candidates_when_empty():
    """general 无候选 → 生成器（LLM 知识）经 clarify 通道回灌 → 出推荐。"""
    result = toolkit(generator=lambda q: "蜀香居、辣妹子").execute("start_decision", {
        "question": "帮我对比两家辣的川菜馆", "scene_hint": "food",
        "slots": {"taste_match": "辣"}, "env": ENV,
    })
    assert result["status"] == "completed"
    assert result["recommendation"]["name"] in ("蜀香居", "辣妹子")
    assert isinstance(result["recommendation"]["details"], dict)  # 名称候选无数值 → 空 details 诚实


def test_start_decision_without_generator_asks_candidates():
    result = toolkit().execute("start_decision", {
        "question": "帮我对比两家川菜馆", "scene_hint": "food", "env": ENV,
    })
    assert result["status"] == "require_action"
    assert result["ask_user"]["request_id"]


def test_start_decision_pending_returns_ask_user():
    result = toolkit().execute("start_decision", {
        "question": "想吃辣", "scene_hint": "food", "env": ENV,
    })
    assert result["status"] == "require_action"
    assert result["ask_user"]["request_id"]  # 无生成器 → 澄清要候选（自由文本）


def test_give_feedback_tool_runs():
    tk = toolkit(generator=lambda q: "蜀香居、辣妹子")
    first = tk.execute("start_decision", {
        "question": "想吃辣", "scene_hint": "food",
        "slots": {"taste_match": "辣"}, "env": ENV,
    })
    result = tk.execute("give_feedback", {"decision_id": first["decision_id"], "complaint": "太贵了"})
    assert result["status"] == "completed"


def test_build_conversation_state_shape():
    state = build_conversation_state([{"role": "user", "text": "想吃辣"}], toolkit())
    assert state["recent_history"] and "experience_material" in state


def test_chat_turn_agent_loop_end_to_end():
    k = kernel()
    tk = AgentToolkit(kernel=k, memory=None, experience_classify=None)
    planner = ScriptedPlanner([
        {"action": "reply", "text": "推荐蜀香居（综合分最高）"},
    ])
    app = DecideApp(kernel=k, planner=planner, toolkit=tk)
    assert app.has_agent
    assert app.chat_turn("想吃辣") == "推荐蜀香居（综合分最高）"
    assert app._history[-2] == {"role": "user", "text": "想吃辣"}


def test_chat_turn_feedback_route():
    k = kernel()
    tk = AgentToolkit(kernel=k, memory=None, experience_classify=None)
    first = k.make_decision(DecisionRequest(
        question="想吃辣", scene_hint="food", candidates=[SHU, LA],
        slots={"taste_match": "辣"}, weights=WEIGHTS, env=ENV,
    ))
    planner = ScriptedPlanner([
        {"action": "tool", "name": "give_feedback",
         "args": {"decision_id": first.decision_id, "complaint": "太贵了"}},
        {"action": "reply", "text": "已按反馈重新推荐"},
    ])
    app = DecideApp(kernel=k, planner=planner, toolkit=tk)
    assert app.chat_turn("太贵了") == "已按反馈重新推荐"


def test_chat_turn_round_limit_fallback():
    app = DecideApp(kernel=kernel(), planner=ScriptedPlanner([]), toolkit=toolkit())
    assert app.chat_turn("随便")  # 脚本耗尽 → done 兜底，不抛异常


def test_chat_turn_without_planner_raises_lookup():
    app = DecideApp(kernel=kernel(), planner=None, toolkit=None)
    with pytest.raises(LookupError):
        app.chat_turn("你好")


def test_planner_json_action_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "请输出下一个动作" in body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"action": "reply", "text": "好的"})}}]})

    planner = LLMDialoguePlannerForTest(httpx.MockTransport(handler))
    assert planner.plan({"recent_history": []}, "你好", []) == {"action": "reply", "text": "好的"}


def test_planner_transport_failure_replies_fallback():
    class Boom(httpx.BaseTransport):
        def handle_request(self, request):
            raise httpx.ConnectError("down")

    planner = LLMDialoguePlannerForTest(httpx.MockTransport(Boom()))
    action = planner.plan({}, "你好", [])
    assert action["action"] == "reply"  # 规划失败 → reply 兜底，不抛


def LLMDialoguePlannerForTest(transport) -> object:
    from decide_agent.models.providers.llm_planner import LLMDialoguePlanner

    return LLMDialoguePlanner(base_url="https://x/v1", model="m", api_key="k",
                              timeout_ms=1000, transport=transport)
