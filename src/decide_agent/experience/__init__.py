"""Experience engine: rules provider + judgment recorder + convergence + metrics.

先天（rules.jsonc 基线，本包）+ 后天（判断流水收敛/蒸馏/三指标/微调导出）。
provider 实现核心域协议 core.decision.base（白名单内，结构化实现零强依赖）。
"""
from decide_agent.experience.converge import BucketStats, bucket_key, converge
from decide_agent.experience.metrics import (
    ShadowRunner,
    agreement,
    coverage,
    drift,
    snapshot_metrics,
)
from decide_agent.experience.provider import ExperienceProvider, UnsupportedShape
from decide_agent.experience.recorder import JudgmentRecorder
from decide_agent.experience.rules_loader import (
    RulesInvalidError,
    RulesNotFoundError,
    SceneRules,
    load_rules,
)

__all__ = [
    "BucketStats",
    "ExperienceProvider",
    "JudgmentRecorder",
    "RulesInvalidError",
    "RulesNotFoundError",
    "SceneRules",
    "ShadowRunner",
    "UnsupportedShape",
    "agreement",
    "bucket_key",
    "converge",
    "coverage",
    "drift",
    "load_rules",
    "snapshot_metrics",
]
