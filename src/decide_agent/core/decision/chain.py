"""Provider chain: probe order -> first available answers; mid-run failure auto-degrades.

Pure scheduling code (zero model calls). Given an ordered provider list:
- probe each: UNAVAILABLE -> skipped, try next (probe exceptions count as unavailable)
- attempt AVAILABLE/DEGRADED: a DEGRADED probe still answers, answer marked degraded
- mid-run exception -> captured as DecisionError, auto-fall to next tier (L4: never raise)
- all exhausted -> ChainResult(ok=False) with structured errors; caller degrades via
  missing_information instead of crashing

Every attempt (ok/skipped/failed) flows to the injected JudgmentSink — the raw
material of the experience flywheel.
"""
import time
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from decide_agent.core.decision.availability import Availability, ProbeResult
from decide_agent.core.decision.base import DecisionProvider
from decide_agent.schemas.error import DecisionError, ErrorSource
from decide_agent.schemas.judgment import JudgmentRecord
from decide_agent.schemas.question import TypedAnswer, TypedQuestion


class _Sink(Protocol):
    def record(self, entry: JudgmentRecord) -> None: ...


class ChainResult(BaseModel):
    answer: TypedAnswer | None = None
    provider: str | None = Field(default=None, description="name of the serving provider")
    skipped: list[str] = Field(default_factory=list, description="unavailable providers, in order")
    failed: list[str] = Field(default_factory=list, description="providers that raised mid-run")
    errors: list[DecisionError] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.answer is not None

    @property
    def degraded(self) -> bool:
        """Answered, but not by the first tier (something above had to be skipped/failed)."""
        return self.answer is not None and bool(self.skipped or self.failed)


class ProviderChain:
    """Ordered judgment sources; the chain owns degrade discipline, nothing else.

    运行中失败自动降（§5.1）：provider 一旦失败即熔断（进程内），后续调用
    直接 Skip——防止每题重复支付失败代价（如远端 400 往返）。
    """

    def __init__(
        self, providers: list[DecisionProvider], sink: _Sink | None = None,
        *, confidence_threshold: float = 0.6,
    ) -> None:
        self._providers = providers
        self._sink = sink
        self._confidence_threshold = confidence_threshold
        self._disabled: dict[str, str] = {}  # name -> reason（熔断缓存）

    @property
    def providers(self) -> list[DecisionProvider]:
        """影子双跑（§5.2）等需要直接访问各 provider 的场景。"""
        return list(self._providers)

    def answer_batch(
        self, questions: list[TypedQuestion], *, state: str | None = None,
    ) -> list[ChainResult]:
        """批量入口（P4 官方批量协议）：支持 answer_many 的 provider 一次多题。

        - 首个可用且具备批量能力的 provider 成功 → 全部填充返回
        - 批量失败 → 熔断该 provider，整组降级到下一 provider
        - 无批量能力可用 → 逐题走 answer（experience 等单题 provider 照常）
        """
        results = [ChainResult() for _ in questions]
        if not questions:
            return results
        for provider in self._providers:
            if provider.name in self._disabled:
                for result in results:
                    result.skipped.append(provider.name)
                continue
            probe = self._safe_probe(provider)
            if probe.availability is Availability.UNAVAILABLE:
                self._disabled[provider.name] = probe.reason or "unavailable"
                for result in results:
                    result.skipped.append(provider.name)
                continue
            batch_fn = getattr(provider, "answer_many", None)
            if batch_fn is None:
                continue  # 无批量能力 → 尝试下一 provider；末尾逐题兜底
            started = time.perf_counter()
            try:
                answers = batch_fn(questions, state=state)
            except Exception as exc:  # noqa: BLE001 — 整组失败 → 熔断降级
                self._disabled[provider.name] = f"disabled after batch failure: {exc}"
                for result in results:
                    result.failed.append(provider.name)
                    result.errors.append(DecisionError(
                        code=f"provider.{provider.name}.batch_failed",
                        source=ErrorSource.PROVIDER,
                        detail=f"{type(exc).__name__}: {exc}",
                        degrade_path="fall to next provider",
                    ))
                continue
            if not isinstance(answers, list) or len(answers) != len(results):
                raise RuntimeError(
                    f"batch answer count mismatch: got {len(answers) if isinstance(answers, list) else type(answers).__name__}, "
                    f"expected {len(results)}")
            latency_ms = (time.perf_counter() - started) * 1000
            for result, answer in zip(results, answers):
                answer.provider = provider.name
                result.answer = answer
                result.provider = provider.name
                self._emit(questions[0], result, provider.name, "ok",
                           answer=answer, confidence=answer.confidence,
                           latency_ms=latency_ms / max(1, len(questions)))
            return results
        # 逐题兜底（无批量能力的 provider）
        for question, result in zip(questions, results):
            single = self.answer(question)
            result.answer = single.answer
            result.provider = single.provider
            result.skipped = single.skipped
            result.failed = single.failed
            result.errors = single.errors
        return results

    def answer(self, question: TypedQuestion) -> ChainResult:
        result = ChainResult()
        for provider in self._providers:
            if provider.name in self._disabled:
                result.skipped.append(provider.name)
                self._emit(question, result, provider.name, "skipped",
                           detail=self._disabled[provider.name])
                continue
            probe = self._safe_probe(provider)
            if probe.availability is Availability.UNAVAILABLE:
                self._disabled[provider.name] = probe.reason or "unavailable"
                result.skipped.append(provider.name)
                self._emit(question, result, provider.name, "skipped", detail=probe.reason)
                continue
            started = time.perf_counter()
            try:
                answer = provider.answer(question)
            except Exception as exc:  # noqa: BLE001 — L4: any failure degrades, never raises
                self._disabled[provider.name] = f"disabled after failure: {type(exc).__name__}: {exc}"
                result.failed.append(provider.name)
                result.errors.append(
                    DecisionError(
                        code=f"provider.{provider.name}.failed",
                        source=ErrorSource.PROVIDER,
                        detail=f"{type(exc).__name__}: {exc}",
                        degrade_path="fall to next provider",
                    )
                )
                self._emit(
                    question, result, provider.name, "failed",
                    detail=f"{type(exc).__name__}: {exc}",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                continue
            answer.provider = provider.name
            answer.degraded = answer.degraded or probe.availability is Availability.DEGRADED
            result.answer = answer
            result.provider = provider.name
            self._emit(
                question, result, provider.name, "ok",
                answer=answer, confidence=answer.confidence,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            # 置信路由：低把握 → 升级下一个 provider（把握度路由核心）
            if answer.confidence >= self._confidence_threshold:
                return result
            result.failed.append(provider.name)
            result.errors.append(DecisionError(
                code=f"provider.{provider.name}.low_confidence",
                source=ErrorSource.PROVIDER,
                detail=f"confidence {answer.confidence} < {self._confidence_threshold}",
                degrade_path="escalate to next provider",
            ))
            continue  # 低把握不定案：换下一级（末级低把握则以最高层答案兜底）
        return result

    def _safe_probe(self, provider: DecisionProvider) -> ProbeResult:
        try:
            return provider.probe()
        except Exception as exc:  # noqa: BLE001 — a broken probe cannot hard-fail the chain
            return ProbeResult(
                availability=Availability.UNAVAILABLE,
                reason=f"probe error: {type(exc).__name__}: {exc}",
            )

    def _emit(
        self,
        question: TypedQuestion,
        result: ChainResult,
        provider: str,
        outcome: str,
        *,
        answer: TypedAnswer | None = None,
        confidence: float | None = None,
        detail: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        if self._sink is None:
            return
        self._sink.record(
            JudgmentRecord(
                ts=datetime.now(UTC).isoformat(),
                scene=question.scene,
                shape=question.shape.value,
                provider=provider,
                outcome=outcome,
                question=question.model_dump(),
                answer=answer.model_dump() if answer else None,
                confidence=confidence,
                detail=detail,
                degraded_from=[*result.skipped, *result.failed],
                latency_ms=latency_ms,
            )
        )
