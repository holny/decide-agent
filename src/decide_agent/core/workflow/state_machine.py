"""Decision state machine (v4 §5.4), re-entrant, with input-required suspension.

created→collecting→[asking⇄collecting]→analyzing→[input-required]→suggested→completed.
State-machine violations are caller bugs (not L4 gaps): they raise.
"""
from enum import Enum


class DecisionState(str, Enum):
    CREATED = "created"
    COLLECTING = "collecting"
    ASKING = "asking"
    ANALYZING = "analyzing"
    INPUT_REQUIRED = "input-required"
    SUGGESTED = "suggested"
    COMPLETED = "completed"


TRANSITIONS: dict[DecisionState, frozenset[DecisionState]] = {
    DecisionState.CREATED: frozenset({DecisionState.COLLECTING}),
    DecisionState.COLLECTING: frozenset({
        DecisionState.ASKING, DecisionState.ANALYZING, DecisionState.INPUT_REQUIRED,
    }),
    DecisionState.ASKING: frozenset({DecisionState.COLLECTING, DecisionState.INPUT_REQUIRED}),
    DecisionState.ANALYZING: frozenset({
        DecisionState.SUGGESTED, DecisionState.INPUT_REQUIRED, DecisionState.COLLECTING,
    }),
    DecisionState.INPUT_REQUIRED: frozenset({DecisionState.COLLECTING, DecisionState.ANALYZING}),
    DecisionState.SUGGESTED: frozenset({DecisionState.COMPLETED}),
    DecisionState.COMPLETED: frozenset(),
}


class InvalidTransition(RuntimeError):
    def __init__(self, current: DecisionState, target: DecisionState) -> None:
        super().__init__(f"illegal transition: {current.value} -> {target.value}")


class StateMachine:
    def __init__(self, initial: DecisionState = DecisionState.CREATED) -> None:
        self._state = initial
        self._history: list[DecisionState] = [initial]

    @property
    def state(self) -> DecisionState:
        return self._state

    @property
    def history(self) -> list[DecisionState]:
        return list(self._history)

    def can(self, target: DecisionState) -> bool:
        return target in TRANSITIONS[self._state]

    def transition(self, target: DecisionState) -> DecisionState:
        if target is self._state:
            return self._state  # 幂等：同状态重复推进为 no-op（Agent 驱动流程的韧性需要）
        if not self.can(target):
            raise InvalidTransition(self._state, target)
        self._state = target
        self._history.append(target)
        return self._state
