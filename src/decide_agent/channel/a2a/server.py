"""A2A adapter (P6): AgentCard 自动生成 + AgentExecutor 桥接（官方 a2a-sdk）。

协议映射（v4 §4.2）：message/send 文本 → make_decision；
Task(input-required) ↔ PendingRequest（taskId→decision_id 映射，续答走 message/send）；
artifacts = DecisionReport（completed）。
"""
import asyncio
import json

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)

from decide_agent.channel.shared.app_like import DecisionAppLike
from decide_agent.schemas.workflow import DecisionRequest


def _text_part(text: str) -> Part:
    return Part(root={"kind": "text", "text": text})  # a2a Part RootModel pydantic 形态


def _data_part(payload: dict) -> Part:
    return Part(root={"kind": "data", "data": payload})


def build_agent_card(url: str = "http://127.0.0.1:8100/") -> AgentCard:
    return AgentCard(
        name="decide-agent",
        description="纯决策 Agent：一次调用 = 一个决策问题，输出标准 DecisionReport",
        url=url,
        version="0.2.0",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[AgentSkill(
            id="make_decision",
            name="make_decision",
            description="接收决策问题原话，输出推荐/备选/缺口披露；input-required 时续答",
            tags=["decision", "recommendation"],
        )],
    )


class DecideAgentExecutor(AgentExecutor):
    """message/send ↔ make_decision/respond 桥接。"""

    def __init__(self, decide: DecisionAppLike) -> None:
        self._decide = decide
        self._task_map: dict[str, dict] = {}  # task_id -> {decision_id, request_id?}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input() or ""
        task_id = context.task_id
        mapping = self._task_map.get(task_id) if task_id else None

        if mapping and mapping.get("request_id"):
            outcome = await asyncio.to_thread(self._decide.respond, 
                mapping["decision_id"], mapping["request_id"], {"value": text},
            )
        else:
            outcome = await asyncio.to_thread(self._decide.make_decision, DecisionRequest(
                question=text, interactive=True,
            ))

        if task_id is None:
            task_id = f"a2a-{outcome.decision_id}"

        if outcome.status == "require_action" and outcome.pending is not None:
            self._task_map[task_id] = {
                "decision_id": outcome.decision_id,
                "request_id": outcome.pending.request_id,
            }
            await event_queue.enqueue_event(Task(
                id=task_id,
                context_id=context.context_id or outcome.decision_id,
                status=TaskStatus(
                    state=TaskState.input_required,
                    message=_as_agent_message(outcome.pending.payload.get("question") or {}),
                ),
                metadata={"decision_id": outcome.decision_id,
                          "request_id": outcome.pending.request_id,
                          "kind": outcome.pending.payload.get("phase", "question")},
            ))
            return

        report = outcome.result.model_dump(mode="json") if outcome.result else {}
        await event_queue.enqueue_event(Task(
            id=task_id,
            context_id=context.context_id or outcome.decision_id,
            status=TaskStatus(state=TaskState.completed),
            artifacts=[Artifact(
                artifact_id=f"report-{outcome.decision_id}",
                name="decision_report",
                parts=[_data_part(report), _text_part(json.dumps(report, ensure_ascii=False))],
            )],
        ))
        self._task_map.pop(task_id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        self._task_map.pop(task_id, None)
        if task_id:
            await event_queue.enqueue_event(Task(
                id=task_id,
                context_id=context.context_id or task_id,
                status=TaskStatus(state=TaskState.canceled),
            ))


def _as_agent_message(question: dict):
    text = str(question.get("text", "需要补充信息"))
    options = question.get("options") or []
    if options:
        text += "\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
    return Message(
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
        message_id=f"msg-{id(text) & 0xFFFFFFFF:08x}",
    )


def build_a2a_app(decide: DecisionAppLike, *, url: str = "http://127.0.0.1:8100/"):
    """官方 a2a-sdk FastAPI 应用（message/send / tasks/*）+ AgentCard。"""
    card = build_agent_card(url)
    executor = DecideAgentExecutor(decide)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    return A2AFastAPIApplication(agent_card=card, http_handler=handler).build()
