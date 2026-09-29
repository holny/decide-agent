"""Agent Loop 对话管理（P7）：LLM 规划动作，现有管道封装为其工具。

角色反转：v4 的固定流水线（意图→收集→打分→合成）降级为 Agent 可调用的工具；
"下一步做什么"由 LLM 每轮根据完整对话状态决定——不再用 if/else 枚举对话情况。

分层：本模块纯同步零网络；DialoguePlanner 协议由 models/providers/llm_planner.py
实现（HTTP I/O），bootstrap 装配。经验引擎定位＝Agent 的参谋（辅助判断材料），
不再是流程门槛。
"""
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

_log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4  # 单用户输入的规划轮上限（防循环失控）

REPLY_ACTION = "reply"


class DialoguePlanner(Protocol):
    """LLM 对话规划器协议：给定状态+输入+工具观察，输出下一个动作。"""

    def plan(self, state: dict, user_input: str, observations: list[dict]) -> dict:
        """返回 {"action": "reply", "text": ...} 或 {"action": "tool", "name": ..., "args": {...}}。"""
        ...


class AgentToolkit:
    """Agent 可调用工具集：现有管道能力的薄封装（全部同步）。

    工具清单（planner 的 system prompt 与此一一对应）：
      experience_baseline(text)           经验引擎参谋材料：关键词先验 + 记忆召回偏好
      recall_memory(query)                用户偏好/历史决策记录
      start_decision(question, ...)       开决策 → 推荐 / 追问（完整管道）
      answer_pending(decision_id, value)  回答追问 → 继续管道
      give_feedback(decision_id, 原因)     负反馈过滤重算
      reset_decision(decision_id)         推倒重来
      reply(text)                         结束轮次，向用户输出
    """

    def __init__(
        self, kernel, memory=None, owner_id: str = "local",
        experience_classify: Callable[[str], dict] | None = None,
        clock: Callable[[], datetime] | None = None,
        display_resolver: Callable[[str], dict] | None = None,
        candidate_generator: Callable[[str], str] | None = None,
    ) -> None:
        self._kernel = kernel
        self._memory = memory
        self._owner_id = owner_id
        self._experience_classify = experience_classify  # 经验引擎参谋（可 None=离线纯规则也缺）
        self._clock = clock or datetime.now
        self._display_resolver = display_resolver  # 维度 key → 显示名（场景模板）
        self._candidate_generator = candidate_generator  # LLM 知识生成候选（general 无数据源时）
        self.last_decision_id: str | None = None  # 跨轮持久：工具产出即记住，供后续轮次回填
        self.current_question: str = ""  # 当前决策主题（生成器上下文）

    def known_environment(self) -> dict:
        """环境事实（Agent 先天上下文）：当前时间（含星期）+ 已知位置（历史记忆）。"""
        now = self._clock()
        weekday = "一二三四五六日"[now.weekday()]
        env: dict[str, Any] = {"当前时间": now.strftime(f"%Y-%m-%d 周{weekday} %H:%M")}
        location = self.known_location()
        if location:
            env["已知位置"] = location
        return env

    def known_location(self) -> str | None:
        """历史记忆里的用户位置（location: 前缀记录，最近优先）。"""
        if self._memory is None:
            return None
        try:
            records = self._memory.export(self._owner_id)
        except Exception:  # noqa: BLE001
            return None
        for record in records:
            record = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
            if str(record.get("status", "active")) != "active":
                continue
            content = str(record.get("content", ""))
            if content.startswith("location:"):
                return content[len("location:"):].strip() or None
        return None

    # ------------------------------------------------------------------ tools

    def experience_baseline(self, text: str) -> dict:
        """经验引擎参谋材料：关键词先验场景 + 用户已知偏好（供 Agent 参考，非门槛）。"""
        material: dict[str, Any] = {}
        if self._experience_classify is not None:
            try:
                material["keyword_prior"] = self._experience_classify(text)
            except Exception as exc:  # noqa: BLE001 — 参谋缺位不阻断（L4）
                _log.warning("experience classify skipped: %s", exc)
        prefs = self.recall_memory(text)
        if prefs:
            material["known_preferences"] = prefs
        return material

    def recall_memory(self, query: str) -> list[dict]:
        if self._memory is None:
            return []
        try:
            records = self._memory.export(self._owner_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning("memory recall skipped: %s", exc)
            return []
        query_terms = [t for t in str(query) if not t.isspace()]
        hits = []
        for record in records:
            record = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
            content = str(record.get("content", ""))
            if any(term in content for term in query_terms[:12]):
                hits.append({"content": content, "kind": str(record.get("kind", ""))})
            if len(hits) >= 5:
                break
        return hits

    def start_decision(self, question: str, scene_hint: str | None = None,
                       slots: dict | None = None, env: dict | None = None) -> dict:
        from decide_agent.schemas.workflow import DecisionRequest

        self.current_question = question
        outcome = self._kernel.make_decision(DecisionRequest(
            question=question, scene_hint=scene_hint,
            slots=slots or {}, env=env or {}, interactive=True,
        ))
        # general（无数据源场景）候选空 + 生成器可用 → LLM 知识生成候选（标注出处），
        # 经 clarify 通道回灌管道打分。模糊到没有品类（NEED_CATEGORY）→ 保持追问不硬造。
        if (outcome.status == "require_action" and outcome.pending is not None
                and outcome.pending.payload.get("phase") == "clarify"
                and self._candidate_generator is not None):
            try:
                generated = str(self._candidate_generator(question))
                if generated.strip() == "NEED_CATEGORY":
                    return self._summarize(outcome)  # 保持追问，Agent 会对话式问品类
                if generated.strip():
                    outcome = self._kernel.respond(
                        outcome.decision_id, outcome.pending.request_id,
                        {"value": generated, "scope": "once"})
                    if outcome.missing_information is not None:
                        outcome.missing_information = [
                            *outcome.missing_information,
                            "候选由模型知识生成（供参考，可替换为你自己的候选）",
                        ]
            except Exception as exc:  # noqa: BLE001 — 生成失败回退澄清（L4）
                _log.warning("candidate generation skipped: %s", exc)
        return self._summarize(outcome)

    def answer_pending(self, decision_id: str, request_id: str, value: str,
                       generate: bool = False) -> dict:
        """澄清应答：value 是需求描述时（generate）→ 先由模型生成具体候选再回灌。"""
        if generate and self._candidate_generator is not None:
            try:
                generated = str(self._candidate_generator(
                    value, context=self.current_question))
                if generated.strip():
                    value = generated
            except Exception as exc:  # noqa: BLE001 — 生成失败用原话（L4）
                _log.warning("answer generate skipped: %s", exc)
        return self._summarize(self._kernel.respond(decision_id, request_id, {"value": value}))

    def give_feedback(self, decision_id: str | None, complaint: str) -> dict:
        return self._summarize(
            self._kernel.give_feedback(decision_id or self.last_decision_id, complaint))

    def reset_decision(self, decision_id: str | None = None) -> dict:
        return self._summarize(self._kernel.reset(decision_id or self.last_decision_id))

    # ------------------------------------------------------------------ summary

    @staticmethod
    def _candidate_details(cand) -> dict:
        """候选具体数值（人话材料）：Agent 转述理由/回答细节追问的事实依据。"""
        details: dict[str, Any] = {}
        get = cand.get if isinstance(cand, dict) else lambda k, d=None: getattr(cand, k, d)
        meters = get("distance_m")
        if meters is not None:
            details["距离"] = f"{meters / 1000:.1f}公里" if meters >= 1000 else f"{int(meters)}米"
        price = get("price_per_person")
        if price is not None:
            details["人均"] = f"¥{float(price):.0f}"
        rating = get("rating")
        if rating is not None:
            details["评分"] = round(float(rating), 1)
        wait = get("wait_min")
        if wait is not None:
            details["排队"] = f"约{int(wait)}分钟"
        location = get("location")
        if isinstance(location, dict) and location.get("lat") is not None:
            details["坐标"] = f"{location['lat']:.4f},{location['lng']:.4f}"
        return details

    def _summarize(self, outcome) -> dict:
        """DecisionOutcome → Agent 可读的紧凑结果（观察回灌材料，含具体数值）。"""
        summary: dict[str, Any] = {
            "status": outcome.status,
            "decision_id": outcome.decision_id,
            "missing_information": outcome.missing_information[:4],
        }
        result = outcome.result
        if result is not None:
            summary["scene"] = result.scene
            display = self._display_resolver(result.scene) if self._display_resolver else {}
            rec = result.recommendation
            if rec is not None:
                summary["recommendation"] = {
                    "name": rec.candidate.name,
                    "score": round(rec.total_score, 3),
                    "details": self._candidate_details(rec.candidate),
                    "dimension_scores": {
                        display.get(d.dimension, d.dimension): round(d.score, 2)
                        for d in rec.dimension_scores
                    },
                }
                summary["alternatives"] = [
                    {"name": alt.candidate.name, "score": round(alt.total_score, 3),
                     "details": self._candidate_details(alt.candidate)}
                    for alt in result.alternatives[:2]
                ]
        pending = outcome.pending
        if pending is not None:
            payload = pending.payload or {}
            question = payload.get("question") or {}
            summary["ask_user"] = {
                "request_id": pending.request_id,
                "text": question.get("text", "需要补充信息"),
                "options": question.get("options", []),
                "recommended": question.get("recommended"),
                "allow_generate": self._candidate_generator is not None,
            }
        return summary

    # ------------------------------------------------------------------ dispatch

    TOOL_NAMES = (
        "experience_baseline", "recall_memory", "start_decision",
        "answer_pending", "give_feedback", "reset_decision",
    )

    def execute(self, name: str, args: dict) -> dict:
        if name not in self.TOOL_NAMES:
            return {"error": f"unknown tool: {name}"}
        try:
            result = getattr(self, name)(**args)
        except TypeError as exc:
            return {"error": f"bad args for {name}: {exc}"}
        except KeyError as exc:
            return {"error": f"unknown decision: {exc}"}
        if result.get("decision_id"):  # 跨轮持久：决策 id 记住，后续轮次 give_feedback 等自动回填
            self.last_decision_id = result["decision_id"]
        return result


def build_conversation_state(history: list[dict], toolkit: AgentToolkit,
                             pending: dict | None = None,
                             recommendation: dict | None = None) -> dict:
    """组装规划器可见的对话状态（紧凑）：历史 + 经验参谋 + 待回答追问 + 当前推荐详情。"""
    state: dict = {
        "recent_history": history[-8:],
        "experience_material": toolkit.experience_baseline(
            " ".join(str(m.get("text", "")) for m in history[-2:])),
    }
    env = toolkit.known_environment()
    if env:
        state["环境"] = env
    if toolkit.last_decision_id:
        state["当前决策"] = {"decision_id": toolkit.last_decision_id}
    if recommendation:
        state["当前推荐"] = recommendation  # 含具体数值（距离/人均/评分）→ 细节追问的事实依据
    if pending:
        state["待回答追问"] = pending  # planner 据此路由 answer_pending（序号→选项原文）
    return state
