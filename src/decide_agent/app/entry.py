"""App entry: the only surface channels touch（四件马甲只认这里，ARCHITECTURE §12.2）.

协议（sync；async 通道自行 run_in_executor 包裹）：
  make_decision(request) -> DecisionOutcome
  respond(decision_id, request_id, response) -> DecisionOutcome
  chat_turn(user_input) -> str          ★ Agent Loop 对话轮（P7；planner 不可用时抛 LookupError → 通道回落旧管道）
  sweep(now) -> 过期挂起清理（通道 sweeper 周期调用）
  render(outcome, fmt?) -> 按配置格式呈现（narrator）
"""
import logging
from collections.abc import Callable
from pathlib import Path

from decide_agent.app.bootstrap import (
    build_dialogue_planner,
    build_kernel,
    resolve_dimension_display,
)
from decide_agent.config.loader import get_output_format, get_output_language
from decide_agent.core.agent.dialogue import (
    REPLY_ACTION,
    AgentToolkit,
    DialoguePlanner,
    build_conversation_state,
)
from decide_agent.core.narrator import OutputFormat, render
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.schemas.workflow import DecisionOutcome, DecisionRequest

_log = logging.getLogger(__name__)


class DecideApp:
    def __init__(self, kernel: DecisionKernel, planner: DialoguePlanner | None = None,
                 toolkit: AgentToolkit | None = None,
                 intent_resolver: Callable[[str, str], str | None] | None = None) -> None:
        self._kernel = kernel
        self._planner = planner
        self._toolkit = toolkit
        self._intent_resolver = intent_resolver  # 测试/宿主可注入意图判定（缺省 kernel.entry_intent）
        self._history: list[dict] = []  # Agent 对话历史（进程内；HTTP 多租户后续持久化）
        self._pending_ref: dict | None = None  # 跨轮追问引用（decision/request id + 选项）
        self._last_summary: dict | None = None  # 跨轮当前推荐详情（距离/人均/评分——细节追问依据）

    @property
    def has_agent(self) -> bool:
        """P7 Agent Loop 是否可用（planner 已装配）。False → 通道回落状态机管道。"""
        return self._planner is not None and self._toolkit is not None

    @property
    def kernel(self) -> DecisionKernel:
        """通道层合法触达点：sweeper/快照/feedback 编排所需（仍禁触 core 内部细节）。"""
        return self._kernel

    def _context_text(self) -> str:
        """连续对话摘要（供意图判定的链上模型区分 answer_pending vs new_decision 等）。"""
        parts: list[str] = []
        if self._pending_ref:
            parts.append(f"助手正在等待用户回答追问：「{self._pending_ref.get('text', '')}」"
                         f"选项：{'、'.join(self._pending_ref.get('options') or [])}。"
                         "用户这条输入很可能就是在回答该追问（除非明确表示要换话题）。")
        if self._last_summary:
            rec = (self._last_summary.get("recommendation") or {})
            parts.append(f"当前已推荐：{rec.get('name', '')}（{self._last_summary.get('scene', '')}场景）")
        if self._history:
            tail = self._history[-2:]
            parts.extend(f"{m.get('role')}:{str(m.get('text', ''))[:60]}" for m in tail)
        return "；".join(parts)

    def _compose(self, user_input: str, summary: dict, hint: str) -> str:
        """LLM 只做措辞：把工具结果转述给用户（带具体数值）。"""
        state = build_conversation_state(
            self._history, self._toolkit, self._pending_ref, self._last_summary)
        observations = [{"action": {"action": "present", "label": hint}, "result": summary}]
        reply = ""
        for _ in range(2):
            action = self._planner.plan(state, user_input, observations)
            if action.get("action") == REPLY_ACTION and str(action.get("text", "")).strip():
                reply = str(action["text"])
                break
            observations.append({"action": action,
                                 "result": {"hint": "只需要 reply 文本转述，不要再调工具。"}})
        if not reply:
            reply = self._deterministic_summary(summary)
        self._history.append({"role": "user", "text": user_input})
        self._history.append({"role": "assistant", "text": reply})
        return reply

    @staticmethod
    def _deterministic_summary(summary: dict) -> str:
        """LLM 措辞失败的确定性兜底：结构化摘要（比裸 JSON 体验好一个量级）。"""
        lines: list[str] = []
        rec = summary.get("recommendation")
        if rec:
            lines.append(f"推荐：{rec.get('name', '')}（综合 {rec.get('score', 0):.2f}）")
            details = rec.get("details") or {}
            if details:
                lines.append("  " + "；".join(f"{k} {v}" for k, v in details.items()))
            for alt in summary.get("alternatives") or []:
                lines.append(f"  备选：{alt.get('name', '')}（{alt.get('score', 0):.2f}）")
        ask = summary.get("ask_user") or (summary.get("待回答追问") if "待回答追问" in summary else None)
        if not rec and isinstance(summary.get("ask_user"), dict):
            ask = summary["ask_user"]
        if ask:
            options = "、".join(ask.get("options") or [])
            lines.append(ask.get("text", "需要补充信息"))
            if options:
                lines.append(f"  选项：{options}")
        for line in summary.get("missing_information") or []:
            lines.append(f"  · {line}")
        return "\n".join(lines) or "请换个说法或给出候选。"

    def chat_turn(self, user_input: str) -> str:
        """对话轮：意图走链上 classify（决策模型语义判定，任意语言）→ 机械映射工具；
        LLM 只负责把工具结果转述成自然语言。planner 未装配 → LookupError 回落旧管道。
        """
        if self._planner is None or self._toolkit is None:
            raise LookupError("dialogue planner not configured")
        intent: str | None = None
        try:
            resolver = self._intent_resolver or self._kernel.entry_intent
            intent = resolver(user_input, self._context_text())
        except Exception as exc:  # noqa: BLE001 — 意图判定失败按新决策处理（L4）
            _log.warning("entry_intent failed: %s", exc)

        # ── 机械映射（模型判语义，代码做分派）──────────────────────────────
        if intent == "swap_next" and self._last_summary:
            self._swap_summary()
            return self._compose(user_input, self._last_summary, "转述换备选后的推荐（含数值）")

        if intent == "dissatisfied" and self._toolkit.last_decision_id:
            outcome = self._toolkit.give_feedback(self._toolkit.last_decision_id, user_input)
            unextracted = any(
                str(line).startswith("未能从反馈中识别")
                for line in outcome.get("missing_information", []))
            if unextracted:  # 原因不在本句 → 反问（LLM 措辞）
                return self._compose(user_input, outcome, "转述需要用户补充不满原因")
            self._last_summary = outcome
            return self._compose(user_input, outcome, "转述按反馈调整后的新推荐（含具体数值）")

        if intent == "answer_pending" and self._pending_ref:
            value = user_input
            options = self._pending_ref.get("options") or []
            match = next((o for o in options if o == value or value in o or o in value), None)
            if match or value.isdigit():
                value = match or value
            outcome = self._toolkit.answer_pending(
                self._pending_ref["decision_id"], self._pending_ref["request_id"], value,
                generate=bool(self._pending_ref.get("allow_generate")))
            self._pending_ref = None
            if outcome.get("recommendation"):
                self._last_summary = outcome
            return self._compose(user_input, outcome, "转述应答后的决策结果（含具体数值）")

        if intent in ("fact_question", "chat"):
            state = build_conversation_state(
                self._history, self._toolkit, self._pending_ref, self._last_summary)
            return self._compose(user_input, state, "闲聊或回答事实性问题（环境材料可答时间/位置）")

        # ── new_decision / 意图未定 → 新决策管道 ──────────────────────────
        summary = self._toolkit.start_decision(question=user_input)
        if summary.get("ask_user"):
            ask = summary["ask_user"]
            self._pending_ref = {
                "decision_id": summary.get("decision_id"),
                "request_id": ask.get("request_id"),
                "text": ask.get("text", ""),
                "options": ask.get("options", []),
                "recommended": ask.get("recommended"),
            }
        elif summary.get("recommendation"):
            self._last_summary = summary
        return self._compose(user_input, summary, "转述决策结果或追问（附编号选项与建议）")

    def _swap_summary(self) -> None:
        """「换一个」：当前推荐与备选轮转（纯呈现层）。"""
        if not self._last_summary:
            return
        rec = self._last_summary.get("recommendation")
        alts = self._last_summary.get("alternatives") or []
        if not rec or not alts:
            return
        self._last_summary["alternatives"] = [rec, *alts[1:]]
        self._last_summary["recommendation"] = alts[0]

    def make_decision(self, request: DecisionRequest) -> DecisionOutcome:
        return self._kernel.make_decision(request)

    def respond(self, decision_id: str, request_id: str, response: dict) -> DecisionOutcome:
        return self._kernel.respond(decision_id, request_id, response)

    def sweep(self, now: float | None = None) -> list[str]:
        return self._kernel.pending.expire_due(now)

    def list_capabilities(self) -> list:
        from decide_agent.app.bootstrap import list_capabilities

        return list_capabilities()

    def render(
        self, outcome: DecisionOutcome, fmt: str | None = None,
        language: str | None = None,
    ) -> dict | str:
        scene = outcome.result.scene if outcome.result else None
        display = resolve_dimension_display(scene) if scene else {}
        config_lang = get_output_language()
        if language:  # 显式参数最高（CLI --lang）
            lang = language
        elif config_lang in ("zh", "en"):  # 配置强制
            lang = config_lang
        else:  # auto：按该次决策输入检测
            lang = outcome.language
        return render(outcome, OutputFormat(fmt or get_output_format()), display=display, language=lang)


def build_app(data_dir: str | Path | None = None, *, decision_mode: str | None = None) -> DecideApp:
    """生产装配入口（bootstrap 唯一全知）。Agent Loop planner 可选：不可用 → 通道回落旧管道。"""
    kernel = build_kernel(data_dir, decision_mode=decision_mode)
    planner, toolkit = build_dialogue_planner(kernel, data_dir)
    return DecideApp(kernel=kernel, planner=planner, toolkit=toolkit)
