"""Convergence skeleton: bucket judgments by feature; n>=5 & sigma<0.15 -> converged.

P1-2 scope: read-only statistics over JudgmentRecords (P1-1 recorder output).
Applying converged means back into rules_override is P1-2+ / distill territory.
"""
import statistics

from pydantic import BaseModel, Field

from decide_agent.schemas.judgment import JudgmentRecord

MIN_SAMPLES_DEFAULT = 5
SIGMA_THRESHOLD_DEFAULT = 0.15


class BucketStats(BaseModel):
    key: str
    n: int
    mean: float
    stdev: float
    converged: bool = Field(description="n >= min_samples and stdev < sigma_threshold")


def bucket_key(record: JudgmentRecord) -> str | None:
    """Feature key: scene|shape|provider|candidate|dimension. None when not bucketable."""
    if record.outcome != "ok":
        return None
    q = record.question or {}
    if q.get("shape") != "score":
        return None
    answer = record.answer or {}
    if not isinstance(answer.get("value"), (int, float)):
        return None
    return "|".join(str(part) for part in (
        record.scene, "score", record.provider,
        q.get("candidate", ""), q.get("dimension", ""),
    ))


def converge(
    records: list[JudgmentRecord],
    min_samples: int = MIN_SAMPLES_DEFAULT,
    sigma_threshold: float = SIGMA_THRESHOLD_DEFAULT,
) -> list[BucketStats]:
    """Group score judgments into buckets and report convergence per bucket."""
    buckets: dict[str, list[float]] = {}
    for record in records:
        key = bucket_key(record)
        if key is None:
            continue
        buckets.setdefault(key, []).append(float(record.answer["value"]))  # type: ignore[index]
    stats: list[BucketStats] = []
    for key, values in sorted(buckets.items()):
        stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
        stats.append(BucketStats(
            key=key,
            n=len(values),
            mean=round(statistics.fmean(values), 4),
            stdev=round(stdev, 4),
            converged=len(values) >= min_samples and stdev < sigma_threshold,
        ))
    return stats


def detect_preference_drift(
    records: list[JudgmentRecord],
    *,
    window_days: int = 30,
    now: float | None = None,
) -> list[dict]:
    """检测偏好漂移：同一候选/维度在近期 vs 早期的评分均值差 > 阈值。

    返回漂移报告列表（有漂移的才有条目）。
    """
    import time as _time

    now = now if now is not None else _time.time()
    window = window_days * 86400.0
    drifts = []

    by_key: dict[str, list[tuple[float, float]]] = {}
    for r in records:
        if r.outcome != "ok" or r.shape != "score":
            continue
        answer = r.answer or {}
        if not isinstance(answer.get("value"), (int, float)):
            continue
        q = r.question or {}
        key = f"{r.scene}|{q.get('candidate', '')}|{q.get('dimension', '')}"
        ts = _parse_ts(r.ts)
        if ts is None:
            continue
        by_key.setdefault(key, []).append((ts, float(answer["value"])))

    for key, data in sorted(by_key.items()):
        if len(data) < 4:
            continue
        recent = [v for ts, v in data if ts >= now - window]
        earlier = [v for ts, v in data if ts < now - window]
        if len(recent) < 2 or len(earlier) < 2:
            continue
        recent_mean = sum(recent) / len(recent)
        earlier_mean = sum(earlier) / len(earlier)
        delta = round(recent_mean - earlier_mean, 4)
        if abs(delta) >= 0.15:
            direction = "↑" if delta > 0 else "↓"
            drifts.append({
                "key": key, "delta": delta,
                "recent_mean": round(recent_mean, 3),
                "earlier_mean": round(earlier_mean, 3),
                "direction": direction,
                "signal": f"偏好漂移 {direction} {abs(delta):.2f}（{key}）",
            })
    return drifts


def _parse_ts(ts_str: str) -> float | None:
    import datetime
    try:
        return datetime.datetime.fromisoformat(ts_str).timestamp()
    except (ValueError, TypeError):
        return None
