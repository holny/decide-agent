"""ContextEngine: per-stage minimal-sufficient context assembly (唯一真源，ARCHITECTURE §9).

每个阶段只看到该阶段的视图；裁剪规则：
- 意图：question 原文 + 场景清单 + 记忆主题摘要（不见候选/打分/历史对话）
- 收集：任务清单 + 已解析值 + 待问项（主体代码，LLM 仅缝隙）
- 决策：结构化 state（候选卡片/环境/槽位/禁忌）（不见自然语言对话）
- 分析：DecisionResult 摘要 + style；原始大 JSON 只留 raw_data_ref
"""
import json
from typing import Any

from decide_agent.schemas.collect import CollectTask
from decide_agent.schemas.decision import DecisionResult


class ContextEngine:
    def for_intent(
        self,
        question: str,
        scenes: list[str],
        memory_topic_summaries: list[str] | None = None,
    ) -> dict:
        return {
            "question": question,
            "scenes": scenes,
            "memory_topics": list(memory_topic_summaries or []),
        }

    def for_collect(
        self,
        tasks: list[CollectTask],
        resolved: dict[str, Any] | None = None,
        pending: list[dict] | None = None,
    ) -> dict:
        return {
            "tasks": [
                {
                    "capability": t.capability,
                    "fallback": t.fallback.value,
                    "depends_on": t.depends_on,
                    "purpose": t.purpose,
                }
                for t in tasks
            ],
            "resolved": dict(resolved or {}),
            "pending": list(pending or []),
        }

    def for_decision(
        self,
        candidates: list[dict],
        env: dict | None = None,
        slots: dict | None = None,
        taboos: list[str] | None = None,
    ) -> dict:
        """产出 DecisionEngine 问题包的 context 信封（候选卡片/环境/槽位/禁忌）。"""
        return {
            "candidate": dict(candidates[0]) if candidates else {},
            "env": dict(env or {}),
            "slots": dict(slots or {}),
            "taboos": list(taboos or []),
        }

    def for_analysis(
        self,
        result: DecisionResult,
        style: str | None = None,
    ) -> dict:
        """大 JSON 不进分析视图：候选原始数据只留 raw_data_ref 指针。"""
        raw_refs = sorted({
            c.get("raw_data_ref") for c in _iter_candidate_dicts(result) if c.get("raw_data_ref")
        })
        return {
            "scene": result.scene,
            "recommendation_summary": _summary(result.recommendation),
            "alternatives": [_summary(a) for a in result.alternatives],
            "confidence": result.confidence,
            "raw_data_refs": raw_refs,
            "style": style,
        }


def _iter_candidate_dicts(result: DecisionResult):
    for rec in [result.recommendation, *result.alternatives]:
        if rec is not None:
            yield rec.candidate.model_dump(mode="json")


def _summary(rec) -> dict | None:
    if rec is None:
        return None
    return {
        "candidate": rec.candidate.name,
        "total_score": rec.total_score,
        "top_factors": [
            {"dimension": ds.dimension, "score": ds.score}
            for ds in sorted(rec.dimension_scores, key=lambda d: d.score * d.weight, reverse=True)[:2]
        ],
    }


def to_json(view: dict) -> str:
    """LLM/日志投递用：确保视图可序列化且紧凑。"""
    return json.dumps(view, ensure_ascii=False, sort_keys=True, default=str)
