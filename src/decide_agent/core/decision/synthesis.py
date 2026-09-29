"""Decision synthesis: weighted merge with missing-dimension renormalisation.

Pure code, zero model (L1). Zero-weight (missing) dimensions contribute nothing
and division by total weight renormalises automatically.
"""
from decide_agent.schemas.decision import DimensionScore


def synthesize(dimension_scores: list[DimensionScore]) -> float:
    """Total = Σ wᵢ×scoreᵢ / Σ wᵢ — missing (weight-0) dims auto-renormalise."""
    if not dimension_scores:
        return 0.0
    total_weight = sum(d.weight for d in dimension_scores)
    if total_weight <= 0:
        return 0.0
    return round(
        sum(d.score * d.weight for d in dimension_scores) / total_weight, 4,
    )


def weights_with_missing(weights: dict[str, float], missing: list[str]) -> dict[str, float]:
    """Ask-aside: zero the weight of missing dims temporarily (renormalised downstream)."""
    return {k: (0.0 if k in missing else v) for k, v in weights.items()}


def cross_validate(
    top_by_score: str | None,
    top_by_choice: str | None,
    confidence: float,
    *,
    boost: float = 0.05,
    cut: float = 0.15,
) -> tuple[float, str | None]:
    """Choice cross-check (v4 §5.1): agree -> boost, disagree -> cut + choice rules.

    平局/不一致以模型整体 Choice 裁决；choice 缺席时原样返回。
    """
    if top_by_choice is None or top_by_score is None:
        return confidence, top_by_score
    if top_by_score == top_by_choice:
        return round(min(1.0, confidence + boost), 4), top_by_choice
    return round(max(0.0, confidence - cut), 4), top_by_choice
