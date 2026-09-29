"""Workflow contracts: PendingRequest / DecisionRequest / DecisionOutcome.

Cross-module + cross-channel data (kernel <-> channels), hence schemas.
"""
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from decide_agent.schemas.decision import DecisionResult


def normalize_candidates(items: list) -> list[dict]:
    """公开入口：kernel 抽取选项后调用（绕过构造器赋值场景）。"""
    return [_normalize_candidate(item) for item in items]


def _normalize_candidate(item: Any) -> dict:
    """「A 咖啡店」这类用户直供选项 → 候选卡片（§10.2③ 通用模式）。"""
    if isinstance(item, str):
        return {"id": item, "name": item}
    if isinstance(item, dict) and "id" not in item and "name" in item:
        return {**item, "id": item["name"]}
    return item


class PendingKind(str, Enum):
    TOOL = "tool"
    QUESTION = "question"


class PendingStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"


class PendingRequest(BaseModel):
    request_id: str
    decision_id: str
    kind: PendingKind
    payload: dict = Field(default_factory=dict, description="tool args / question body")
    timeout_s: float | None = None
    budget_cost: float = 0.0
    created_ts: float
    status: PendingStatus = PendingStatus.PENDING
    response: dict | None = None
    answered_ts: float | None = None


class DecisionRequest(BaseModel):
    question: str
    scene_hint: str | None = None
    candidates: list[Any] = Field(
        default_factory=list,
        description="用户/宿主直供候选（字符串自动规范化为卡片）→ collect 跳过，§10.2③",
    )

    @field_validator("candidates")
    @classmethod
    def _normalize_candidates(cls, value: list) -> list:
        return [_normalize_candidate(item) for item in value]
    env: dict = Field(default_factory=dict)
    slots: dict = Field(default_factory=dict, description="pre-extracted params (taste/budget...)")
    weights: dict[str, float] = Field(default_factory=dict, description="dimension -> weight; keys are dims")
    subject: str | None = Field(
        default=None,
        description="决策对象：帮朋友/家人/同事等第三方选择时填称谓；None=用户本人（记忆隔离）",
    )
    joint_with: str | None = Field(
        default=None,
        description="共同决策对象：「帮我和xx选」时填 xx——用户与 xx 的记忆都召回，冲突维度按中性+披露",
    )
    interactive: bool = False
    ask_budget: float = 1.0
    decision_id: str | None = None
    language: str | None = Field(default=None, description="输出语言显式指定；None=按问题文本自动检测")
    inline_tools: list[str] = Field(
        default_factory=list,
        description="请求内联工具声明（v4 §4.4①）：调用方可提供的标准能力名列表（如 ['location']），"
                    "声明后引擎跳过 collect 对该能力的内置调用",
    )


class DecisionOutcome(BaseModel):
    decision_id: str
    status: str = Field(description="completed | require_action")
    state: str = Field(description="DecisionState value")
    result: DecisionResult | None = None
    pending: PendingRequest | None = None
    missing_information: list[str] = Field(default_factory=list)
    fingerprint_skips: list[str] = Field(default_factory=list, description="stages reused via fingerprint")
    budget_left: float = 0.0
    learned_memory: list[dict] = Field(default_factory=list, description="MemoryRecord dumps（双向：宿主入库+自有层）")
    language: str = Field(default="zh", description="输出语言（按输入自动检测或显式指定）")
    ranked_all: list[dict] = Field(default_factory=list, description="完整排序 [{candidate_id, name, score}]")
    error: dict | None = Field(default=None, description="structured DecisionError dump when fatal")


def outcome_payload(outcome: DecisionOutcome) -> dict[str, Any]:
    return outcome.model_dump(mode="json")
