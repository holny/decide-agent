"""JudgmentRecord: one chain attempt, the raw material of the experience flywheel.

Cross-module data (core.decision.chain -> experience.recorder), hence a schema.
Written as append-only JSONL, owner-bucketed.
"""
from pydantic import BaseModel, Field


class JudgmentRecord(BaseModel):
    ts: str = Field(description="ISO-8601 UTC timestamp")
    decision_id: str | None = Field(default=None, description="stamped by kernel-level sink wrapper")
    scene: str = ""
    shape: str = Field(description="QuestionShape value: classify|score|extract|verify")
    provider: str = Field(description="provider name, or the one skipped/failed")
    outcome: str = Field(description="ok | skipped | failed")
    question: dict = Field(default_factory=dict, description="TypedQuestion dump")
    answer: dict | None = Field(default=None, description="TypedAnswer dump when ok")
    confidence: float | None = None
    detail: str | None = Field(default=None, description="probe reason / exception text")
    degraded_from: list[str] = Field(default_factory=list, description="higher tiers skipped/failed before")
    latency_ms: float | None = None
