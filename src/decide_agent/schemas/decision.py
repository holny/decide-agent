"""DecisionResult: recommendation + alternatives with dimension-based reasons."""
from pydantic import BaseModel, Field

from decide_agent.schemas.candidate import Candidate
from decide_agent.schemas.question import Question


class DimensionScore(BaseModel):
    dimension: str
    score: float = Field(ge=0, le=1)
    weight: float
    reason: str | None = None


class Recommendation(BaseModel):
    candidate: Candidate
    total_score: float
    dimension_scores: list[DimensionScore]
    reason: str


class DecisionResult(BaseModel):
    scene: str
    recommendation: Recommendation | None = None
    alternatives: list[Recommendation] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    follow_up: Question | None = Field(
        default=None, description="set when confidence is low: ask instead of answering"
    )
    filtered_out: list[str] = Field(
        default_factory=list, description="candidate ids removed by taboo/hard filters"
    )
    evidence_scope: dict[str, str] = Field(
        default_factory=dict,
        description="proof scope (P8): winning-candidate dimension → evidence class "
        "(rule-hit | model-judged | neutral | degraded | missing)",
    )
