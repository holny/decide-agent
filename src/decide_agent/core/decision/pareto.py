"""帕累托前沿：淘汰被支配候选，只保留不可同时超越的选项。"""
from decide_agent.schemas.decision import DimensionScore


def pareto_filter(
    candidates: list[dict],
    scores: dict[str, list[DimensionScore]],
) -> tuple[list[str], list[str]]:
    """返回 (frontier, dominated)。被支配 = 存在另一候选每维不差且至少一维严格更好。"""
    frontier, dominated = [], []
    for cand in candidates:
        cid = str(cand.get("id", cand.get("name", "")))
        ds = scores.get(cid)
        if ds is None:
            frontier.append(cand)
            continue
        is_dominated = False
        for other in candidates:
            oid = str(other.get("id", other.get("name", "")))
            if oid == cid:
                continue
            ods = scores.get(oid)
            if ods is None:
                continue
            if _dominates(ods, ds):
                is_dominated = True
                break
        if is_dominated:
            dominated.append(cid)
        else:
            frontier.append(cid)
    return frontier, dominated


def _dominates(a: list[DimensionScore], b: list[DimensionScore]) -> bool:
    """候选 a 是否支配候选 b（每维 ≥ 且至少一维 >）。"""
    a_map = {d.dimension: d.score for d in a}
    b_map = {d.dimension: d.score for d in b}
    shared = set(a_map) & set(b_map)
    if not shared:
        return False
    return all(a_map[d] >= b_map[d] for d in shared) and any(
        a_map[d] > b_map[d] for d in shared
    )
