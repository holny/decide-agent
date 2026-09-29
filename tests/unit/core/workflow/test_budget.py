"""Budget tests: injectable clock, expire/not-expire, remaining time."""
from decide_agent.core.workflow.budget import Budgets


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_unbounded_never_expires():
    clock = FakeClock()
    budgets = Budgets(clock=clock)
    budget = budgets.start("decide")
    clock.now = 10_000
    assert budget.expired() is False


def test_expires_after_timeout():
    clock = FakeClock()
    budgets = Budgets(decide_s=5, clock=clock)
    budget = budgets.start("decide")
    clock.now = 4.9
    assert budget.expired() is False
    assert budget.remaining() == pytest_approx(0.1)
    clock.now = 5.0
    assert budget.expired() is True
    assert budget.remaining() == 0.0


def test_stages_independent():
    clock = FakeClock()
    budgets = Budgets(collect_s=1, decide_s=100, clock=clock)
    collect = budgets.start("collect")
    budgets.start("decide")
    clock.now = 2.0
    assert collect.expired() is True
    assert budgets.get("decide").expired() is False


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
