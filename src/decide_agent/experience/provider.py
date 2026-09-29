"""ExperienceProvider: rules-driven DecisionProvider — the always-available先天基线.

Implements core.decision.base.DecisionProvider structurally (sync). P1 shapes:
- score: rules.jsonc 维度规则；无命中 → 中性 0.5 + 低置信（§5.2）
- classify: skill.jsonc keywords 规则兜底（§10.4：规则兜底，语义级留给 decision_model）
其它形状 raise UnsupportedShape（链降级到下一级，L4）。

Input envelope (question.context, flattened for rule evaluation):
  {"candidate": {...candidate fields...}, "env": {...weather 等...}, "slots": {...taste_match/budget...}}
"""
import json
import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from decide_agent.core.decision.availability import ProbeResult
from decide_agent.experience.rules_loader import (
    ExprRule,
    RulesInvalidError,
    SceneRules,
    StepsRule,
    TagSetRule,
    load_rules,
)
from decide_agent.schemas.question import QuestionShape, TypedAnswer, TypedQuestion


class UnsupportedShape(RuntimeError):
    """This provider does not serve the requested shape."""


class _Outcome(BaseModel):
    value: float
    basis: str | None
    confidence: float
    neutral: bool


class ExperienceProvider:
    name = "experience"

    def __init__(self, search_dirs: list[Path], scene_cache: bool = True) -> None:
        self._dirs = [Path(d) for d in search_dirs]
        self._cache: dict[str, SceneRules] = {}
        self._cache_on = scene_cache

    def probe(self) -> ProbeResult:
        if not self._dirs:
            return ProbeResult(
                availability="degraded", reason="no rules search dirs configured",
            )
        return ProbeResult()

    def answer(self, question: TypedQuestion) -> TypedAnswer:
        if question.shape is QuestionShape.SCORE:
            return self._answer_score(question)
        if question.shape is QuestionShape.CLASSIFY:
            return self._answer_classify(question)
        if question.shape is QuestionShape.EXTRACT:
            return self._answer_extract(question)
        raise UnsupportedShape(f"experience provider handles score/classify/extract, got {question.shape}")

    # 抽取模式：按优先级排列，(slot_name, regex, mapped_value or None=原文)
    _EXTRACT_PATTERNS: ClassVar[list] = [
        ("taste_match", r"(清淡|不吃辣|不能吃辣|不辣)", "清淡"),
        ("taste_match", r"(想吃辣|无辣不欢|麻辣|重辣|中辣|微辣)", "辣"),
        ("taste_match", r"辣", "辣"),
        ("budget", r"预算\s*(\d+)", None),
        ("distance_pref", r"(近一点|就近|不远|附近)", "近"),
    ]

    def _answer_extract(self, question: TypedQuestion) -> TypedAnswer:
        text = question.text or ""
        slots: dict[str, str] = {}
        for slot_name, pattern, mapped in self._EXTRACT_PATTERNS:
            if slot_name in slots:
                continue
            match = re.search(pattern, text)
            if match:
                slots[slot_name] = mapped if mapped else (match.group(1) if match.lastindex else match.group(0))
        if not slots:
            return TypedAnswer(shape=QuestionShape.EXTRACT, value=None,
                               confidence=0.2, provider=self.name)
        return TypedAnswer(
            shape=QuestionShape.EXTRACT, value=json.dumps(slots, ensure_ascii=False),
            confidence=0.8, provider=self.name,
        )

    # ------------------------------------------------------------------ score

    def _answer_score(self, question: TypedQuestion) -> TypedAnswer:
        rules = self._rules_for(question.scene)
        inputs = self._flatten(question)
        rule = rules.rule_for(question.dimension or "")
        outcome = self._evaluate(rule, inputs, rules, tags=self._tags(inputs))
        return TypedAnswer(
            shape=QuestionShape.SCORE,
            value=round(outcome.value, 3),
            confidence=outcome.confidence,
            basis=outcome.basis,
            provider=self.name,
            degraded=outcome.neutral,
        )

    # ------------------------------------------------------------------ classify

    def _answer_classify(self, question: TypedQuestion) -> TypedAnswer:
        text = question.text or ""
        scenes = question.context.get("scenes") or self._discover_scenes()
        distribution = {scene: self._keyword_score(text, scene) for scene in scenes}
        top = max(distribution, key=distribution.get) if distribution else None
        confidence = float(distribution.get(top, 0.0)) if top else 0.0
        return TypedAnswer(
            shape=QuestionShape.CLASSIFY,
            value=top,
            distribution=distribution,
            confidence=round(confidence, 3),
            basis="keyword rules" if confidence >= 0.5 else "no keyword hit",
            provider=self.name,
        )

    def _discover_scenes(self) -> list[str]:
        scenes: list[str] = []
        for directory in self._dirs:
            if not Path(directory).exists():
                continue
            for child in sorted(Path(directory).iterdir()):
                if (child / "skill.jsonc").exists() and child.name not in scenes:
                    scenes.append(child.name)
        return scenes

    def _keyword_score(self, text: str, scene: str) -> float:
        """规则兜底打分：场景名/关键词命中。0.6 起步，多命中加成（P0 SceneAgent 对齐）。"""
        keywords = self._keywords_for(scene)
        hits = sum(1 for kw in keywords if kw and kw in text)
        if hits == 0 and scene not in text:
            return 0.2
        return round(min(0.9, 0.6 + 0.1 * (hits - 1 if hits > 0 else 0)), 3)

    def _keywords_for(self, scene: str) -> list[str]:
        for directory in self._dirs:
            path = Path(directory) / scene / "skill.jsonc"
            if path.exists():
                try:
                    from decide_agent.common.jsonc import load as load_jsonc

                    data = load_jsonc(path) or {}
                    return [str(k) for k in data.get("keywords", [])]
                except Exception:  # noqa: BLE001 — 数据缺陷不阻断（L4）
                    return []
        return []

    # ------------------------------------------------------------------ internals

    def _rules_for(self, scene: str) -> SceneRules:
        if scene in self._cache and self._cache_on:
            return self._cache[scene]
        rules = load_rules(scene, self._dirs)  # raises NotFound/Invalid -> chain degrades
        if self._cache_on:
            self._cache[scene] = rules
        return rules

    @staticmethod
    def _flatten(question: TypedQuestion) -> dict:
        ctx = question.context or {}
        return {**(ctx.get("slots") or {}), **(ctx.get("env") or {}), **(ctx.get("candidate") or {})}

    @staticmethod
    def _tags(inputs: dict) -> set[str]:
        tags = inputs.get("tags")
        return set(tags) if isinstance(tags, (list, tuple, set)) else set()

    def _evaluate(
        self, rule, inputs: dict, rules: SceneRules, *, tags: set[str],
    ) -> _Outcome:
        if rule is None:  # 无规则 → 中性 0.5 + 低置信（§5.2）
            return _Outcome(value=0.5, basis=None, confidence=rules.neutral_confidence, neutral=True)
        if isinstance(rule, TagSetRule):
            value, basis = rule.evaluate(inputs, tags)
            neutral = value == rule.on_missing_slot.score and basis is None and rule.on_missing_slot.basis is None
            return _Outcome(
                value=value, basis=basis,
                confidence=rules.neutral_confidence if neutral else rules.confidence,
                neutral=neutral,
            )
        if isinstance(rule, StepsRule):
            return _Outcome(value=rule.evaluate(inputs), basis=None, confidence=rules.confidence, neutral=False)
        if isinstance(rule, ExprRule):
            return _Outcome(value=rule.evaluate(inputs), basis=None, confidence=rules.confidence, neutral=False)
        raise RulesInvalidError(f"unknown rule kind: {getattr(rule, 'kind', '?')}")  # pragma: no cover
