"""MCP adapter (P4.5): make_decision tool over stdio / Streamable HTTP.

continuation 循环（v4 §4.2）：首调传 question；返回 require_action.actions 后，
宿主以 decision_id+request_id+response 续调同一工具（无长连接，状态存 server）。
elicitation 增链（P4-4，红线 1）：宿主以 elicit_answers=True 声明能力后，question
可走 ctx.elicit（扁平 schema 单字段）；decline/cancel → 降级顺延（continuation 兜底）。
内核同步——FastMCP 在线程池执行同步/异步工具，符合并发模型（§12.2）。
"""
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from decide_agent.channel.shared.app_like import DecisionAppLike
from decide_agent.schemas.workflow import DecisionOutcome, DecisionRequest

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class ElicitAnswer(BaseModel):
    """扁平 schema（红线 1：仅原始类型，合批=多属性并列）。"""

    answer: str = Field(default="", description="你的回答")


def map_elicitation(outcome: DecisionOutcome, action: str, data: dict | None) -> tuple[str, dict]:
    """三态映射：accept→respond 续答；decline/cancel→降级顺延（continuation 兜底）。"""
    if action == "accept" and data and str(data.get("answer", "")).strip():
        return "respond", {"value": str(data["answer"])}
    return "fallback", {
        "caveat": f"elicitation {action or 'none'} → 降级顺延（continuation 兜底）",
    }


async def make_decision_impl(decide: DecisionAppLike, args: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    question = str(args.get("question", ""))
    decision_id = args.get("decision_id")
    request_id = args.get("request_id")
    scope = str(args.get("scope", "once"))
    elicit_answers = bool(args.get("elicit_answers"))

    if decision_id and request_id:
        outcome = decide.respond(decision_id, request_id, {"value": args.get("response"), "scope": scope})
        return outcome.model_dump(mode="json")
    if not question and not args.get("candidates"):
        return {"error": {"code": "input.invalid",
                          "detail": "question or decision_id+request_id required"}}

    outcome = decide.make_decision(DecisionRequest(
        question=question,
        scene_hint=args.get("scene_hint"),
        interactive=bool(args.get("interactive", True)),
        weights=args.get("weights") or {},
        candidates=args.get("candidates") or [],  # §10.2③：用户直供选项 → 通用模式
    ))

    if elicit_answers and outcome.status == "require_action" \
            and outcome.pending is not None and ctx is not None \
            and outcome.pending.payload.get("kind", "question") == "question":
        question_body = outcome.pending.payload.get("question") or {}
        try:
            result = await ctx.elicit(
                str(question_body.get("text", "请补充信息")), ElicitAnswer,
            )
            data = {"answer": result.data.answer} if result.data is not None else {}
            mode, extra = map_elicitation(outcome, result.action, data)
        except Exception as exc:  # noqa: BLE001 — elicitation 通道故障也是缺口（L4）
            mode, extra = "fallback", {"caveat": f"elicitation failed: {exc} → continuation 兜底"}
        if mode == "respond":
            answered = decide.respond(
                outcome.decision_id, outcome.pending.request_id,
                {**extra, "scope": scope},
            )
            return answered.model_dump(mode="json")
        outcome = outcome.model_copy(deep=True)
        outcome.pending.payload["caveat"] = extra["caveat"]

    return outcome.model_dump(mode="json")


def build_mcp_server(decide: DecisionAppLike) -> "FastMCP":
    from mcp.server.fastmcp import Context, FastMCP

    mcp: FastMCP = FastMCP("decide-agent", host="127.0.0.1", port=8100)

    @mcp.tool()
    async def make_decision(
        question: str = "",
        decision_id: str | None = None,
        request_id: str | None = None,
        response: str | None = None,
        scope: str = "once",
        scene_hint: str | None = None,
        interactive: bool = True,
        weights: dict[str, float] | None = None,
        candidates: list[Any] | None = None,
        elicit_answers: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """一次调用 = 一个决策问题。

        首调：传 question（如「想吃辣，预算100」）；用户已给出几个选项时，
        直接传 candidates（字符串或 {name, description,...}）→ 通用模式比较。
        返回 require_action 时：取 pending.request_id，以 decision_id + request_id +
        response 续调本工具（continuation 循环）；response.scope ∈ once|session|always。
        宿主支持 elicitation 时传 elicit_answers=True：问题改为标准反向收集，
        decline/cancel 自动降级顺延。
        """
        return await make_decision_impl(decide, {
            "question": question, "decision_id": decision_id, "request_id": request_id,
            "response": response, "scope": scope, "scene_hint": scene_hint,
            "interactive": interactive, "weights": weights,
            "elicit_answers": elicit_answers,
        }, ctx)

    @mcp.tool()
    def list_capabilities() -> list[dict[str, Any]]:
        """列出可用能力（收集调度寻址视图）。"""
        return [c.model_dump() for c in decide.list_capabilities()]

    return mcp


def run(decide: DecisionAppLike, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8100) -> None:
    server = build_mcp_server(decide)
    server.settings.host = host
    server.settings.port = port
    server.run(transport=transport)
