"""权重四层解析（v4 §5.5）：base → deploy → owner 调制 → request 覆盖。

生效：owner 调制 clamp [基线×0.5, 基线×2.0]（防跑飞）→ 归一化；
effective_weights 落 EffectiveWeights 可披露（"价格权重上调 30%（来源：反馈×3）"）。
"""
from decide_agent.schemas.weights import EffectiveWeights, WeightModulation

CLAMP_LOW = 0.5
CLAMP_HIGH = 2.0


def resolve(
    scene: str,
    base: dict[str, float],
    modulations: list[WeightModulation] | None = None,
    request_override: dict[str, float] | None = None,
) -> EffectiveWeights:
    effective = dict(base)
    modulated: list[str] = []
    disclosure: list[str] = []
    for m in modulations or []:
        if m.scene != scene or m.status != "active" or m.dimension not in effective:
            continue
        baseline = effective[m.dimension]
        raw = baseline + m.delta * m.strength
        clamped = min(max(raw, baseline * CLAMP_LOW), baseline * CLAMP_HIGH)
        if abs(clamped - baseline) > 1e-9:
            modulated.append(m.dimension)
            direction = "上调" if clamped > baseline else "下调"
            percent = abs(clamped - baseline) / baseline * 100 if baseline else 0.0
            disclosure.append(
                f"{m.dimension} {direction} {percent:.0f}%（来源：{m.source.value}×{m.evidence_count}）",
            )
        effective[m.dimension] = clamped
    if request_override:  # 请求级最高优先（不 clamp——宿主显式指令）
        effective.update(request_override)

    total = sum(effective.values())
    if total > 0:
        effective = {k: round(v / total, 4) for k, v in effective.items()}
    return EffectiveWeights(
        scene=scene, weights=effective,
        modulated=sorted(set(modulated)), disclosure=disclosure,
    )
