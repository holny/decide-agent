"""Clock budgets: collect/decide/analysis each get a timeout; exceed -> degrade, not fail.

Sync stop-watch with an injectable clock for deterministic tests.
"""
import time


class StageBudget:
    def __init__(self, timeout_s: float | None, clock: callable = time.monotonic) -> None:  # type: ignore[valid-type]
        self._timeout_s = timeout_s
        self._clock = clock
        self._started_at: float | None = None

    def start(self) -> "StageBudget":
        self._started_at = self._clock()
        return self

    def expired(self) -> bool:
        if self._timeout_s is None or self._started_at is None:
            return False
        return (self._clock() - self._started_at) >= self._timeout_s

    def remaining(self) -> float | None:
        if self._timeout_s is None or self._started_at is None:
            return None
        return max(0.0, self._timeout_s - (self._clock() - self._started_at))


class Budgets:
    """One budget per stage; None = unbounded."""

    STAGES = ("collect", "decide", "analysis")

    def __init__(
        self,
        collect_s: float | None = None,
        decide_s: float | None = None,
        analysis_s: float | None = None,
        clock: callable = time.monotonic,  # type: ignore[valid-type]
    ) -> None:
        self._clock = clock
        self._limits = {
            "collect": collect_s, "decide": decide_s, "analysis": analysis_s,
        }
        self._stage_budgets: dict[str, StageBudget] = {
            stage: StageBudget(limit, clock) for stage, limit in self._limits.items()
        }

    def start(self, stage: str) -> StageBudget:
        budget = self._stage_budgets[stage]
        return budget.start()

    def get(self, stage: str) -> StageBudget:
        return self._stage_budgets[stage]
