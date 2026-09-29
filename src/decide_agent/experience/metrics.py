"""Experience metrics: shadow runner（影子双跑）+ 三指标计算（coverage/agreement/drift）。

影子双跑调度（v4 §5.2）：同一问题让链上各 provider 各答一次——
agreement 指标与权重影子对照通道（§5.5c）共用其结果；只建议不自动切模式（红线 9）。
"""
import json
import time
from pathlib import Path
from typing import Any

from decide_agent.schemas.judgment import JudgmentRecord

DRIFT_WINDOW_DAYS = 7


class ShadowRunner:
    """对同一 TypedQuestion 让链上所有可用 provider 各答一次。"""

    def __init__(self, chain) -> None:
        self._chain = chain

    def compare(self, question) -> dict[str, Any]:
        providers = getattr(self._chain, "providers", None) or []
        answers: dict[str, Any] = {}
        for provider in providers:
            try:
                answer = provider.answer(question)
                answers[provider.name] = {
                    "value": answer.value,
                    "confidence": answer.confidence,
                }
            except Exception as exc:  # noqa: BLE001 — 影子失败照常披露（L4）
                answers[provider.name] = {"error": str(exc)}
        scored = {
            name: info["value"] for name, info in answers.items()
            if isinstance(info.get("value"), int | float)
        }
        agree = len(set(scored.values())) <= 1 and len(scored) >= 2
        return {"question": question.model_dump(mode="json"),
                "answers": answers, "agree": agree, "scored": scored}


def coverage(records: list[JudgmentRecord]) -> float:
    """规则覆盖率：experience 的 score 判定中有 basis（命中规则）的占比。"""
    scored = [
        r for r in records
        if r.outcome == "ok" and r.shape == "score" and r.provider == "experience"
    ]
    if not scored:
        return 0.0
    hits = sum(1 for r in scored if (r.answer or {}).get("basis"))
    return round(hits / len(scored), 4)


def agreement(shadow_results: list[dict]) -> float:
    """双跑一致率（+用户反馈校准由 feedback 通道回写，weight×3 在融合层）。"""
    if not shadow_results:
        return 0.0
    return round(sum(1 for r in shadow_results if r.get("agree")) / len(shadow_results), 4)


def drift(records: list[JudgmentRecord], now: float | None = None) -> float:
    """7 天漂移：最近 7 天 score 均值 与 之前同长度窗口均值 的绝对差。"""
    now = now if now is not None else time.time()
    window = DRIFT_WINDOW_DAYS * 86400.0

    def _mean_of(records_slice: list[JudgmentRecord]) -> float | None:
        values = [
            float(r.answer["value"]) for r in records_slice
            if r.outcome == "ok" and r.shape == "score"
            and isinstance((r.answer or {}).get("value"), int | float)
        ]
        return sum(values) / len(values) if values else None

    recent = _mean_of([r for r in records if _ts(r) >= now - window])
    prior = _mean_of([r for r in records if now - 2 * window <= _ts(r) < now - window])
    if recent is None or prior is None:
        return 0.0
    return round(abs(recent - prior), 4)


def _ts(record: JudgmentRecord) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(record.ts).timestamp()
    except (TypeError, ValueError):  # pragma: no cover — 坏时间戳忽略
        return 0.0


def snapshot_metrics(path: Path, *, coverage_v: float, agreement_v: float, drift_v: float) -> dict:
    """metrics.json 落盘（users/{owner}/metrics.json；global 仅匿名聚合，红线 7）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "ts": time.time(),
        "experience_coverage": coverage_v,
        "experience_agreement": agreement_v,
        "drift": drift_v,
        "suggest_experience_mode": coverage_v >= 0.9 and agreement_v >= 0.85 and drift_v < 0.05,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return data


def load_metrics(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text("utf-8"))
