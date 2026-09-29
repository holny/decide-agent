"""Workflow kernel: state machine / fingerprints / pending / budgets / make_decision.

The orchestrator — the only core zone allowed to import domain modules (via
constructor injection at assembly time). Fully synchronous (§12.2).
"""
from decide_agent.core.workflow.budget import Budgets, StageBudget
from decide_agent.core.workflow.fingerprint import StageFingerprint
from decide_agent.core.workflow.kernel import Collector, DecisionKernel
from decide_agent.core.workflow.pending import PendingNotFoundError, PendingRegistry
from decide_agent.core.workflow.state_machine import (
    TRANSITIONS,
    DecisionState,
    InvalidTransition,
    StateMachine,
)

__all__ = [
    "TRANSITIONS",
    "Budgets",
    "Collector",
    "DecisionKernel",
    "DecisionState",
    "InvalidTransition",
    "PendingNotFoundError",
    "PendingRegistry",
    "StageBudget",
    "StageFingerprint",
    "StateMachine",
]
