"""MemoryEntry: user preference / scene memory record (P0, kept for legacy path).

MemoryRecord: v4 §6 full entity (P2 三源记忆 ID 制) — scope/kind/保鲜/淘汰语义.
"""
from enum import Enum

from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    type: str = Field(description="'preference' | 'taboo' | 'history'")
    scene: str = Field(default="global", description="'food' | 'travel' | 'exam' | 'global' ...")
    content: str = Field(description="e.g. 嗜辣 / 预算50-80 / 不吃香菜")
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: str = Field(default="user_feedback", description="how this memory was learned")
    pending: bool = Field(
        default=False, description="low-confidence entries wait for explicit confirmation"
    )


class MemoryScope(str, Enum):
    USER = "user"  # 长期（always-scope 才落）
    SESSION = "session"  # 会话
    EPHEMERAL = "ephemeral"  # 即用即弃，不落库


class MemoryKind(str, Enum):
    PREFERENCE = "preference"
    TABOO = "taboo"
    HISTORY = "history"
    FACT = "fact"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    PENDING = "pending"  # 低置信挂起 / 矛盾待确认
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemoryRecord(BaseModel):
    id: str
    owner_id: str
    scope: MemoryScope = MemoryScope.USER
    kind: MemoryKind
    scene: str = "global"
    content: str
    confidence: float = Field(default=0.6, ge=0, le=1)
    strength: float = Field(default=1.0, ge=0, description="保鲜强度，随 half_life 衰减")
    half_life_days: float | None = Field(default=None, description="口味~180d/预算~60d（按 kind 缺省）")
    created_at: float
    last_used_at: float | None = None
    used_count: int = 0
    source: str = Field(default="declare", description="declare|feedback|extract|external|shadow")
    status: MemoryStatus = MemoryStatus.ACTIVE
    superseded_by: str | None = None
