"""Memory policy: freshness decay, archival, LRU eviction, supersede/conflict demotion.

默认半衰期（v4 §6）：口味 preference~180d / 预算类 fact~60d / history~30d / taboo~365d。
矛盾降权：冲突 → status=PENDING + confidence 减半，确认后恢复（"以后都改吗？"）。
"""
from decide_agent.schemas.memory import MemoryKind, MemoryRecord, MemoryStatus

DEFAULT_HALF_LIFE_DAYS: dict[str, float] = {
    MemoryKind.PREFERENCE.value: 180.0,
    MemoryKind.FACT.value: 60.0,
    MemoryKind.HISTORY.value: 30.0,
    MemoryKind.TABOO.value: 365.0,
}

ARCHIVE_BELOW_STRENGTH = 0.05
CONFIRM_THRESHOLD = 2  # 冲突观察制：一致观察 ≥2 次才更新长期


def half_life_for(record: MemoryRecord) -> float:
    if record.half_life_days is not None:
        return float(record.half_life_days)
    return DEFAULT_HALF_LIFE_DAYS.get(record.kind.value, 90.0)


def decayed_strength(record: MemoryRecord, now: float) -> float:
    """strength × 0.5^(age/half_life)；从未使用按 created_at 起算。"""
    half_life = half_life_for(record)
    anchor = record.last_used_at or record.created_at
    age_days = max(0.0, (now - anchor) / 86400.0)
    return round(record.strength * (0.5 ** (age_days / half_life)), 4)


def touch(record: MemoryRecord, now: float) -> None:
    """LRU：使用即保鲜（used_count+1, last_used_at=now）。"""
    record.used_count += 1
    record.last_used_at = now


def should_archive(record: MemoryRecord, now: float) -> bool:
    return decayed_strength(record, now) < ARCHIVE_BELOW_STRENGTH


def sweep_archival(records: list[MemoryRecord], now: float) -> list[str]:
    """归档过期记忆，返回被归档的 id 列表。"""
    archived = []
    for record in records:
        if record.status is MemoryStatus.ACTIVE and should_archive(record, now):
            record.status = MemoryStatus.ARCHIVED
            archived.append(record.id)
    return archived


def evict_lru(records: list[MemoryRecord], keep: int, now: float) -> list[str]:
    """超容量的 ACTIVE 记忆按 LRU（最久未用/最低强度优先）归档，返回被淘汰 id。"""
    active = [r for r in records if r.status is MemoryStatus.ACTIVE]
    if len(active) <= keep:
        return []
    ordered = sorted(active, key=lambda r: (r.last_used_at or r.created_at, r.strength))
    to_evict = ordered[: len(active) - keep]
    for record in to_evict:
        record.status = MemoryStatus.ARCHIVED
        _ = now  # 归档不依赖 now，参数保留给调用侧日志
    return [r.id for r in to_evict]


def supersede(old: MemoryRecord, new_id: str) -> None:
    old.status = MemoryStatus.SUPERSEDED
    old.superseded_by = new_id


def demote_on_conflict(record: MemoryRecord) -> None:
    """矛盾降权：挂起 + 置信减半，等确认（"以后都改吗？"）。"""
    record.status = MemoryStatus.PENDING
    record.confidence = round(record.confidence * 0.5, 4)


def promote_on_confirm(record: MemoryRecord, new_content: str | None = None) -> None:
    record.status = MemoryStatus.ACTIVE
    record.confidence = min(1.0, record.confidence * 2.0)
    if new_content is not None:
        record.content = new_content
