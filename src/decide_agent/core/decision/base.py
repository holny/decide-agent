"""Provider framework contracts: DecisionProvider + JudgmentSink.

Protocols are defined by the consumer (decision domain). Implementations:
- core/experience/provider (in-core, rules-driven)
- models/providers/{systemone_adapter, llm_provider} (adapter layer, network I/O)

Whitelisted dependency: implementers -> core.decision.base only (import-linter).
Both protocols are sync — core concurrency model is fully synchronous (§12.2).
"""
from typing import Protocol, runtime_checkable

from decide_agent.core.decision.availability import ProbeResult
from decide_agent.schemas.judgment import JudgmentRecord
from decide_agent.schemas.question import TypedAnswer, TypedQuestion


@runtime_checkable
class DecisionProvider(Protocol):
    """One judgment source answering the four atomic question shapes."""

    name: str

    def probe(self) -> ProbeResult:
        """Self-report availability: transport I/O lives in the implementation."""
        ...

    def answer(self, question: TypedQuestion) -> TypedAnswer:
        """Answer one atomic question; raise on failure (chain catches + degrades)."""
        ...


@runtime_checkable
class JudgmentSink(Protocol):
    """Receives every chain attempt (ok/skipped/failed). experience.recorder implements."""

    def record(self, entry: JudgmentRecord) -> None: ...
