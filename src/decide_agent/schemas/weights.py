"""Weights contracts: WeightModulation（千人千权生长实体）+ EffectiveWeights 披露。"""
from enum import Enum

from pydantic import BaseModel, Field


class ModulationSource(str, Enum):
    DECLARE = "declare"  # 显式声明（"我在乎便宜"）
    FEEDBACK = "feedback"  # 接受/拒绝归因
    SHADOW = "shadow"  # 影子对照（双跑不一致且用户采纳模型结果）


class WeightModulation(BaseModel):
    owner_id: str
    scene: str
    dimension: str
    delta: float = Field(description="有符号调整量（叠加到模板基线，clamp 后归一化）")
    strength: float = Field(default=1.0, ge=0, description="强度，随半衰期衰减")
    half_life_days: float = 90.0
    source: ModulationSource = ModulationSource.DECLARE
    evidence_count: int = 0
    updated_at: float
    status: str = "active"  # active | retired


class EffectiveWeights(BaseModel):
    scene: str
    weights: dict[str, float]
    modulated: list[str] = Field(default_factory=list, description="被 owner 级调制过的维度")
    disclosure: list[str] = Field(default_factory=list, description="可披露轨迹（红线：落决策轨迹）")