"""Question: structured follow-up question (options preferred over free text).

TypedQuestion/TypedAnswer: the four atomic question shapes crossing the
DecisionProvider protocol (classify/score/extract/verify). Payload typing is
refined in P1-3; the envelope is stable now.
"""
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Question(BaseModel):
    text: str
    options: list[str] = Field(default_factory=list, description="2-4 selectable options preferred")
    recommended: str | None = Field(default=None, description="经验引擎/记忆建议的选项（参谋，非门槛）")
    allow_free_text: bool = True
    reason: str | None = Field(default=None, description="why we ask (low-confidence dimension)")


class QuestionShape(str, Enum):
    CLASSIFY = "classify"  # intent: probability distribution over scenes
    SCORE = "score"  # candidate x dimension
    EXTRACT = "extract"  # slot extraction
    VERIFY = "verify"  # taboo / contradiction / memory check


class TypedQuestion(BaseModel):
    shape: QuestionShape
    scene: str = ""
    text: str | None = Field(default=None, description="raw utterance for classify/extract")
    candidate: str | None = Field(default=None, description="score target")
    dimension: str | None = Field(default=None, description="score target dimension")
    slot: str | None = Field(default=None, description="extract/verify target slot")
    context: dict[str, Any] = Field(default_factory=dict)


class TypedAnswer(BaseModel):
    shape: QuestionShape
    value: float | str | None = Field(default=None, description="scalar or label answer")
    distribution: dict[str, float] | None = Field(
        default=None, description="full probability distribution (classify)"
    )
    confidence: float = 0.0
    basis: str | None = Field(default=None, description="evidence trail, feeds narrator")
    provider: str | None = Field(default=None, description="stamped by the chain")
    degraded: bool = Field(default=False, description="provider self-reported degraded quality")
