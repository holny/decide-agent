"""Narrator: the optional presentation stage. Templates only — zero LLM (L7).

三格式（config.output.format，v4 §3 可选阶段语义）：
- json_minimal（默认）：纯结构化输出，剥除叙述字段（reason/问题话术）
- json_full：完整结构化 + 理由串/caveats/维度显示名（模板组装，仍零 LLM）
- text：叠加人读文本（P0 _format_decision/_format_question 迁入）
"""
from enum import Enum

from decide_agent.core.narrator.i18n import strings
from decide_agent.schemas.workflow import DecisionOutcome


class OutputFormat(str, Enum):
    JSON_MINIMAL = "json_minimal"
    JSON_FULL = "json_full"
    TEXT = "text"


def render(
    outcome: DecisionOutcome,
    fmt: OutputFormat = OutputFormat.JSON_MINIMAL,
    *,
    display: dict[str, str] | None = None,
    language: str = "zh",
) -> dict | str:
    """Render a kernel outcome per format. display: dimension key -> 中文名."""
    display = display or {}
    if fmt is OutputFormat.TEXT:
        return _render_text(outcome, display, language)
    if fmt is OutputFormat.JSON_FULL:
        return _render_full(outcome, display)
    return _render_minimal(outcome)


def _render_minimal(outcome: DecisionOutcome) -> dict:
    data = outcome.model_dump(mode="json")
    result = data.get("result")
    if result:
        rec = result.get("recommendation")
        if rec:
            rec.pop("reason", None)
        for alt in result.get("alternatives") or []:
            alt.pop("reason", None)
    pending = data.get("pending")
    if pending:
        pending.get("payload", {}).pop("question", None)  # 不组装话术
    return data


def _render_full(outcome: DecisionOutcome, display: dict[str, str]) -> dict:
    data = outcome.model_dump(mode="json")
    result = data.get("result")
    if result:
        for rec in [result.get("recommendation"), *(result.get("alternatives") or [])]:
            if not rec:
                continue
            rec.setdefault("reason", "")
            for ds in rec.get("dimension_scores") or []:
                ds["dimension_display"] = display.get(ds["dimension"], ds["dimension"])
    pending = data.get("pending")
    if pending and not pending.get("payload", {}).get("question"):
        pending["payload"]["question"] = {"text": "请补充信息", "allow_free_text": True}
    return data


def build_reason(
    display: dict[str, str], scores, prefs: list[str] | None = None,
    language: str = "zh",
) -> str:
    """理由串模板（P0 synthesizer.build_reason 迁入）：top 因子 + 偏好呼应，零 LLM。"""
    S = strings(language)
    ranked = sorted(scores, key=lambda d: d.score * d.weight, reverse=True)
    parts = []
    for d in ranked[:3]:
        label = display.get(d.dimension, d.dimension)
        verdict = (
            S["verdict_strong"] if d.score >= 0.7
            else S["verdict_decent"] if d.score >= 0.5
            else S["verdict_fair"]
        )
        parts.append(f"{label} {verdict}" if language == "en" else f"{label}{verdict}")
    joiner = ", " if language == "en" else "、"
    reason = joiner.join(parts)
    if prefs:
        suffix = S["pref_suffix"].format(prefs=(joiner.join(prefs)))
        reason += suffix
    return reason


def _render_text(outcome: DecisionOutcome, display: dict[str, str], language: str = "zh") -> str:
    S = strings(language)
    if outcome.status == "require_action":
        question = (outcome.pending.payload if outcome.pending else {}).get("question") or {}
        lines = [question.get("text", "需要补充信息")]
        for index, option in enumerate(question.get("options") or [], 1):
            lines.append(f"  {index}. {option}")  # 编号选项：用户可直接回序号（kernel 侧映射回原文）
        recommended = question.get("recommended")
        if recommended:
            lines.append(f"  💡 建议：{recommended}（根据你的历史偏好，直接回序号即可）")
        if question.get("allow_free_text", True):
            lines.append("  · 其他（请输入）")
        return "\n".join(lines)

    result = outcome.result
    if result is None or result.recommendation is None:
        if result is not None and result.scene == "chat":
            return S["chat_reply"]
        reason = "；".join(outcome.missing_information) or "无可比较候选"
        return S["cannot"].format(reason=reason)

    rec = result.recommendation
    lines = [S["recommend"].format(
    name=rec.candidate.name, score=f"{rec.total_score:.2f}",
    band=_score_band(rec.total_score, language))]
    for ds in rec.dimension_scores:
        lines.append(f"  · {display.get(ds.dimension, ds.dimension)}: {ds.score:.2f} (权重 {ds.weight})")
    lines.append(S["reason"].format(
        reason=build_reason(display, rec.dimension_scores, language=language)))
    scope = outcome.result.evidence_scope or {}
    weak = [display.get(dim, dim) for dim, cls in scope.items() if cls in ("neutral", "degraded", "missing")]
    if weak:
        lines.append(S["evidence_note"].format(dims="、".join(weak)))
    for alt in result.alternatives:
        lines.append(S["alternative"].format(
            name=alt.candidate.name, score=f"{alt.total_score:.2f}",
            band=_score_band(alt.total_score, language),
            reason=build_reason(display, alt.dimension_scores, language=language)))
    lines.append(S["scale"].format(band=_score_band(rec.total_score, language)))
    if rec.total_score < 0.5:
        lines.append(S["low_hint"])
    lines.append(S["accept"])
    if outcome.missing_information:
        lines.append(S["note"].format(items="；".join(outcome.missing_information)))
    if outcome.ranked_all and len(outcome.ranked_all) > 1:
        ranking = " > ".join(
            f"{r.get('name', r.get('candidate_id', ''))} {r.get('score', 0):.2f}"
            for r in outcome.ranked_all
        )
        lines.append(f"完整排序：{ranking}")
    return "\n".join(lines)


def _score_band(score: float, language: str = "zh") -> str:
    """综合分档位解读（0~1）。"""
    S = strings(language)
    if score >= 0.8:
        return S["band_very"]
    if score >= 0.65:
        return S["band_good"]
    if score >= 0.5:
        return S["band_ok"]
    return S["band_low"]
