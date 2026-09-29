"""Collect contracts: capability / task / report / collector outcome.

Cross-module (collect <-> workflow/kernel <-> channel), hence schemas.
"""
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Side(str, Enum):
    SERVER = "server"  # 引擎侧数据源
    MCP = "mcp"  # MCP 工具
    CLIENT = "client"  # 宿主侧能力（GPS 等）
    MEMORY = "memory"  # 记忆检索


class DegradePolicy(str, Enum):
    ASK_ONCE = "ask_once"  # 问一次（A拍：阻塞缺口收集启动即问）
    DEGRADE = "degrade"  # 缺分维度权重置零（打分层归一化）
    NEUTRAL = "neutral"  # 中性 0.5 + caveat
    BLOCK = "block_to_clarify"  # 唯一硬结局：候选空 → 转澄清


class Capability(BaseModel):
    name: str
    side: Side
    description: str = ""
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)


class CollectTask(BaseModel):
    capability: str
    purpose: str = ""
    fallback: DegradePolicy = DegradePolicy.NEUTRAL
    depends_on: list[str] = Field(default_factory=list, description="DAG 边（capability 名）")
    optional: bool = False
    bind: str | None = Field(
        default=None,
        description="env 绑定路径（如 weather.condition）：把结果内字段绑为 env 键",
    )
    precision: str | None = Field(
        default=None,
        description="精度需求：city（默认，IP 定位够用）| precise（需要 GPS/LBS）",
    )


class CollectResult(BaseModel):
    capability: str
    ok: bool = False
    value: Any = None
    caveat: str | None = None
    error: dict | None = None
    needs_ask: bool = False


class CollectReport(BaseModel):
    results: dict[str, CollectResult] = Field(default_factory=dict)
    batches: list[list[str]] = Field(default_factory=list, description="拓扑批次（披露）")
    disclosed: list[str] = Field(default_factory=list)
    needs: list[CollectResult] = Field(default_factory=list, description="ask_once 失败项")
    blocked: bool = False


class CollectorOutcome(BaseModel):
    """Kernel-facing collect result: candidates + env enrichment + disclosures."""

    candidates: list[dict] = Field(default_factory=list)
    env: dict = Field(default_factory=dict)
    disclosed: list[str] = Field(default_factory=list)
    needs: list[dict] = Field(default_factory=list, description="A拍待问项（capability/slot/question）")
    blocked: bool = False
