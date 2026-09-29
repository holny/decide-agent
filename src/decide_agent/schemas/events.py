"""Event contracts: SSE 包络 {decision_id, seq, type, payload, ts}（v4 §4.3）。"""
from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    STAGE = "stage"  # 状态机阶段变化
    PENDING_REQUEST = "pending_request"  # 挂起（问题/工具）
    EXPIRED = "expired"  # 挂起超时（sweeper 推送）
    COMPLETED = "completed"
    PROGRESS = "progress"  # 内部运算进度（Streamable HTTP progress 对应物）


class EventEnvelope(BaseModel):
    decision_id: str
    seq: int = Field(ge=0, description="decision 内单调递增，SSE after_seq 续传锚点")
    type: EventType
    payload: dict = Field(default_factory=dict)
    ts: float