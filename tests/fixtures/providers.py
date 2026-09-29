"""Deterministic providers & sinks for unit tests — no network, fully seeded."""
from decide_agent.core.decision.availability import ProbeResult
from decide_agent.schemas.judgment import JudgmentRecord
from decide_agent.schemas.question import TypedAnswer, TypedQuestion


class FakeProvider:
    """Scripted DecisionProvider: canned probe / answer / raised error."""

    def __init__(
        self,
        name: str,
        probe: ProbeResult | Exception | None = None,
        answer: TypedAnswer | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self._probe = probe if probe is not None else ProbeResult()
        self._answer = answer
        self._error = error
        self.calls = 0

    def probe(self) -> ProbeResult:
        if isinstance(self._probe, Exception):
            raise self._probe
        return self._probe

    def answer(self, question: TypedQuestion) -> TypedAnswer:
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self._answer is not None:
            return self._answer
        return TypedAnswer(shape=question.shape, value=0.9, confidence=0.8, provider=self.name)


class ListSink:
    """In-memory JudgmentSink for assertions."""

    def __init__(self) -> None:
        self.records: list[JudgmentRecord] = []

    def record(self, entry: JudgmentRecord) -> None:
        self.records.append(entry)


def score_question(scene: str = "food") -> TypedQuestion:
    return TypedQuestion(
        shape="score", scene=scene, candidate="蜀香居", dimension="口味",
    )
