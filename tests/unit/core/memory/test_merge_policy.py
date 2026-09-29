"""Merge & policy tests: three-source priority, conflict observation, decay/LRU/supersede."""
from decide_agent.core.memory import policy
from decide_agent.core.memory.merge import SourceMerger
from decide_agent.schemas.memory import MemoryKind, MemoryRecord, MemoryScope, MemoryStatus


def rec(rid="m1", owner="u1", content="嗜辣", kind=MemoryKind.PREFERENCE, scene="food",
        created=100.0, used=None, strength=1.0) -> MemoryRecord:
    return MemoryRecord(
        id=rid, owner_id=owner, scope=MemoryScope.USER, kind=kind, scene=scene,
        content=content, created_at=created, last_used_at=used, strength=strength,
    )


# ------------------------------------------------------------------ merge

def test_priority_external_shadows_session_shadows_user():
    merger = SourceMerger()
    user = rec("m_user", content="嗜辣")
    session = rec("m_sess", content="微辣", kind=MemoryKind.PREFERENCE)
    external = rec("m_ext", content="不能吃辣", kind=MemoryKind.PREFERENCE)
    report = merger.merge([user], [session], [external], now=200.0)
    contents = [r.content for r in report.records]
    assert contents[0] == "不能吃辣"  # external 最前（生效视图优先级序）
    assert "嗜辣" in contents and "微辣" in contents
    assert all(r.last_used_at == 200.0 for r in report.records)  # 触碰 LRU


def test_conflict_demotes_and_requires_confirmations():
    merger = SourceMerger(confirm_threshold=2)
    user = rec("m_user", content="嗜辣")
    external = rec("m_ext", content="不能吃辣", kind=MemoryKind.PREFERENCE)
    report = merger.merge([user], [], [external], now=200.0)
    assert user.status is MemoryStatus.PENDING  # 挂起，不静默覆盖
    assert user.confidence < 0.6  # 置信减半
    assert report.caveats and "1/2" in report.caveats[0]
    report2 = merger.merge([user], [], [external], now=201.0)
    assert "2/2" in report2.caveats[0] and "已达阈值" in report2.caveats[0]  # 二次一致观察 → 允许更新


def test_no_conflict_when_external_agrees():
    merger = SourceMerger()
    user = rec("m_user", content="嗜辣")
    external = rec("m_ext", content="嗜辣", kind=MemoryKind.PREFERENCE)
    report = merger.merge([user], [], [external], now=200.0)
    assert report.conflicts == [] and user.status is MemoryStatus.ACTIVE


# ------------------------------------------------------------------ policy

def test_decay_halving():
    r = rec(created=0.0, used=None)
    r.half_life_days = 180.0
    now = 180 * 86400.0
    assert policy.decayed_strength(r, now) == 0.5  # 一个半衰期 → 一半
    assert policy.decayed_strength(r, 0.0) == 1.0


def test_archival_below_threshold():
    r = rec(created=0.0)
    r.half_life_days = 30.0
    assert policy.should_archive(r, 400 * 86400.0) is True  # 13 个半衰期
    assert policy.should_archive(r, 86400.0) is False


def test_lru_eviction():
    records = [rec(f"m{i}", used=100.0 + i, strength=1.0) for i in range(5)]
    evicted = policy.evict_lru(records, keep=3, now=1000.0)
    assert len(evicted) == 2
    assert records[0].id in evicted and records[1].id in evicted  # 最久未用先淘汰
    assert records[0].status is MemoryStatus.ARCHIVED


def test_supersede_and_confirm():
    old = rec("m_old")
    policy.supersede(old, "m_new")
    assert old.status is MemoryStatus.SUPERSEDED and old.superseded_by == "m_new"
    pending = rec("m_p")
    pending.status = MemoryStatus.PENDING
    policy.promote_on_confirm(pending, "不吃香菜")
    assert pending.status is MemoryStatus.ACTIVE and pending.content == "不吃香菜"
