"""Collect domain: capability registry + DAG scheduler + degrade policies.

调度器自身零智能判断；收集是可选阶段——
调用方自带候选 / 模板清单为空时跳过（v4 §5.6）。
"""
from decide_agent.core.collect.collector import TemplateCollector, load_info_needs
from decide_agent.core.collect.registry import CapabilityRegistry
from decide_agent.core.collect.scheduler import CircularDependency, CollectScheduler

__all__ = [
    "CapabilityRegistry",
    "CircularDependency",
    "CollectScheduler",
    "TemplateCollector",
    "load_info_needs",
]
