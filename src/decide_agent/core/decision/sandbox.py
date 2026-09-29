"""Sandbox sensitivity: ask the user only when unknowns could flip the ranking (L5).

P4-3 实算语义（与 kernel 一致）：缺失维度以中性 0.5×w 占位参与合成；
其真实值未知 → 每候选贡献 a_c + w·u_c（u 独立 U[0,1]）。
翻转条件（对 topo）：w·(u_ch − u_top) > a_top − a_ch；d = a_top − a_ch：
  d ≤ 0 → 必翻（p=1）；d ≥ w → 稳健（p=0）；否则 P = (1 − d/w)²/2（三角分布）。
快速启发式、只在提问决策时启用；校准分布随 P4 provider 置信接入。
"""
from decide_agent.core.decision.synthesis import synthesize
from decide_agent.schemas.decision import DimensionScore

ScoresByCandidate = dict[str, list[DimensionScore]]


def _known_terms(ds: list[DimensionScore], missing: set[str]) -> float:
    """已知维度贡献（缺失维度剥离 0.5·w 占位）。"""
    return sum(d.score * d.weight for d in ds if d.dimension not in missing)


def flip_probabilities(
    scores_by_candidate: ScoresByCandidate,
    weights: dict[str, float],
    missing: list[str],
) -> list[tuple[str, float]]:
    """[(dimension, p)]：缺失维度可能翻转 topo 的概率，按 p 降序。"""
    missing_set = set(missing)
    terms = {
        cid: _known_terms(ds, missing_set)
        for cid, ds in scores_by_candidate.items()
    }
    totals = {cid: synthesize(ds) for cid, ds in scores_by_candidate.items()}
    if len(totals) < 2:
        return []
    top_id = max(totals, key=totals.get)  # type: ignore[arg-type]
    a_top = terms[top_id]

    result: list[tuple[str, float]] = []
    for dimension in sorted(missing):
        w = weights.get(dimension, 0.0)
        if w <= 0:
            continue
        probability = 0.0
        for cid in scores_by_candidate:
            if cid == top_id:
                continue
            d = a_top - terms[cid]
            if d <= 0:
                p = 1.0
            elif d >= w:
                p = 0.0
            else:
                p = (1 - d / w) ** 2 / 2
            probability = max(probability, p)
        if probability > 0.0:
            result.append((dimension, round(probability, 3)))
    result.sort(key=lambda t: t[1], reverse=True)
    return result


def potential_flip_dims(
    scores_by_candidate: ScoresByCandidate,
    weights: dict[str, float],
    missing: list[str],
) -> list[str]:
    """兼容入口：p>0 即「可能翻转」。"""
    return [dim for dim, _ in flip_probabilities(scores_by_candidate, weights, missing)]
