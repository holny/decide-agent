"""DecisionEngine: provider chain + four question packets + synthesis/gating/sandbox.

Pure logic, no network I/O. P1-3 lands: engine packets, synthesis, gating,
sandbox, cross-validation; transport skeletons live in models/providers.
"""
from decide_agent.core.decision.availability import Availability, ProbeResult, key_probe, worst
from decide_agent.core.decision.base import DecisionProvider, JudgmentSink
from decide_agent.core.decision.chain import ChainResult, ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.decision.gating import ConfidenceVerdict, evaluate
from decide_agent.core.decision.sandbox import potential_flip_dims
from decide_agent.core.decision.synthesis import cross_validate, synthesize, weights_with_missing

__all__ = [
    "Availability",
    "ChainResult",
    "ConfidenceVerdict",
    "DecisionEngine",
    "DecisionProvider",
    "JudgmentSink",
    "ProbeResult",
    "ProviderChain",
    "cross_validate",
    "evaluate",
    "key_probe",
    "potential_flip_dims",
    "synthesize",
    "weights_with_missing",
    "worst",
]
