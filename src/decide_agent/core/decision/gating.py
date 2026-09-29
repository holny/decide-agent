"""Confidence gate: the second decision axis — low confidence means ask, not answer.

P0 rule verbatim: penalty per unscorable dimension + too-few candidates.
P4+: TypeSafe-calibrated confidence drives this instead (see ARCHITECTURE §5.1).
"""
from pydantic import BaseModel, Field


class ConfidenceVerdict(BaseModel):
    confidence: float
    should_ask: bool
    missing_dimensions: list[str] = Field(default_factory=list)


def evaluate(
    missing_dimensions: list[str],
    n_candidates: int,
    threshold: float,
    penalty_per_missing: float = 0.40,
) -> ConfidenceVerdict:
    confidence = 1.0
    confidence -= penalty_per_missing * len(missing_dimensions)
    if n_candidates < 3:
        confidence -= 0.10 * (3 - n_candidates)
    confidence = round(max(confidence, 0.0), 4)
    return ConfidenceVerdict(
        confidence=confidence,
        should_ask=confidence < threshold,
        missing_dimensions=missing_dimensions,
    )
