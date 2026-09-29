"""State machine tests: happy path, suspension loops, violation raises."""
import pytest

from decide_agent.core.workflow.state_machine import (
    DecisionState as S,
)
from decide_agent.core.workflow.state_machine import (
    InvalidTransition,
    StateMachine,
)


def test_happy_path():
    sm = StateMachine()
    for step in (S.COLLECTING, S.ANALYZING, S.SUGGESTED, S.COMPLETED):
        sm.transition(step)
    assert sm.state is S.COMPLETED
    assert sm.history == [S.CREATED, S.COLLECTING, S.ANALYZING, S.SUGGESTED, S.COMPLETED]


def test_asking_collecting_loop():
    sm = StateMachine()
    sm.transition(S.COLLECTING)
    sm.transition(S.ASKING)
    sm.transition(S.COLLECTING)  # answered -> back to collecting
    assert sm.state is S.COLLECTING


def test_asking_can_suspend_to_input_required():
    sm = StateMachine()
    sm.transition(S.COLLECTING)
    sm.transition(S.ASKING)
    sm.transition(S.INPUT_REQUIRED)  # A拍问题挂起等宿主
    sm.transition(S.COLLECTING)      # respond 恢复
    assert sm.state is S.COLLECTING


def test_input_required_from_both_stages_and_resume():
    for origin, resume in ((S.COLLECTING, S.COLLECTING), (S.ANALYZING, S.ANALYZING)):
        sm = StateMachine()
        sm.transition(S.COLLECTING)
        if origin is S.ANALYZING:
            sm.transition(S.ANALYZING)
        sm.transition(S.INPUT_REQUIRED)
        sm.transition(resume)
        assert sm.state is resume


def test_analyzing_reenters_collecting_for_incremental_recollection():
    sm = StateMachine()
    sm.transition(S.COLLECTING)
    sm.transition(S.ANALYZING)
    sm.transition(S.COLLECTING)  # 缺口反哺增量补采
    assert sm.state is S.COLLECTING


def test_violation_raises():
    sm = StateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition(S.COMPLETED)  # created -> completed illegal
    with pytest.raises(InvalidTransition):
        sm.transition(S.SUGGESTED)  # created -> suggested illegal


def test_completed_is_terminal():
    sm = StateMachine()
    for step in (S.COLLECTING, S.ANALYZING, S.SUGGESTED, S.COMPLETED):
        sm.transition(step)
    assert sm.can(S.COMPLETED) is False
