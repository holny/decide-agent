"""MemoryService facade tests: gating, quota offload, learn, maintain, privacy, migration."""
import json

from decide_agent.common import migrations
from decide_agent.core.memory.manager import MemoryService, build_memory_store
from decide_agent.schemas.memory import MemoryKind, MemoryRecord, MemoryScope, MemoryStatus


def service(tmp_path, **kw) -> MemoryService:
    store = build_memory_store(tmp_path / "evolved", "u1")
    return MemoryService(store, raw_dir=tmp_path / "raw", **kw)


# ------------------------------------------------------------------ remember gating

def test_remember_active_vs_pending_vs_ephemeral(tmp_path):
    svc = service(tmp_path)
    active = svc.remember("u1", "嗜辣", MemoryKind.PREFERENCE, "food", now=100.0, confidence=0.9)
    assert active.status is MemoryStatus.ACTIVE

    pending = svc.remember("u1", "预算80", MemoryKind.FACT, "food", now=100.0, confidence=0.4)
    assert pending.status is MemoryStatus.PENDING  # 低置信挂起

    ephemeral = svc.remember("u1", "临时上下文", MemoryKind.FACT, "food",
                             now=100.0, scope=MemoryScope.EPHEMERAL)
    assert ephemeral is not None and svc._store.count("u1") == 2  # ephemeral 不落库


def test_confirm_pending_promotes(tmp_path):
    svc = service(tmp_path)
    pending = svc.remember("u1", "预算80", MemoryKind.FACT, "food", now=100.0, confidence=0.4)
    confirmed = svc.confirm_pending("u1", pending.id)
    assert confirmed.status is MemoryStatus.ACTIVE and confirmed.confidence == 0.8


def test_supersede_replaces_content(tmp_path):
    svc = service(tmp_path)
    old = svc.remember("u1", "嗜辣", MemoryKind.PREFERENCE, "food", now=100.0)
    new = svc.supersede_with("u1", old, "改吃清淡", now=200.0)
    assert old.status is MemoryStatus.SUPERSEDED and old.superseded_by == new.id
    assert new.content == "改吃清淡" and new.status is MemoryStatus.ACTIVE


# ------------------------------------------------------------------ external quota (P2-3)

def test_external_quota_offloads_oversized(tmp_path):
    svc = service(tmp_path)
    big = "x" * 9000  # >8KB
    accepted, caveats = svc.ingest_external([big, {"content": "嗜辣"}])
    assert "raw_data_ref" in accepted[0]
    assert (tmp_path / "raw").exists()  # 卸载落盘
    assert accepted[1] == {"content": "嗜辣"}
    assert any("单份超限" in c for c in caveats)


def test_external_quota_total_limit(tmp_path):
    svc = service(tmp_path)
    items = [{"n": i, "pad": "y" * 6000} for i in range(8)]  # 8×6KB > 32KB
    accepted, caveats = svc.ingest_external(items)
    assert len(accepted) < len(items)
    assert any("超总量限额" in c for c in caveats)


# ------------------------------------------------------------------ recall + learn

def test_recall_merges_sources_with_external(tmp_path):
    svc = service(tmp_path)
    svc.remember("u1", "嗜辣", MemoryKind.PREFERENCE, "food", now=100.0)
    external = [MemoryRecord(
        id="ext1", owner_id="u1", scope=MemoryScope.EPHEMERAL, kind=MemoryKind.PREFERENCE,
        scene="food", content="不能吃辣", created_at=200.0,
    )]
    report = svc.recall("u1", scene="food", external=external, now=200.0)
    assert report.records[0].content == "不能吃辣"  # external 生效优先
    assert report.caveats  # 冲突披露


def test_learn_writes_owner_layer(tmp_path):
    svc = service(tmp_path)
    learned = [MemoryRecord(
        id="seed", owner_id="any", scope=MemoryScope.USER, kind=MemoryKind.PREFERENCE,
        scene="food", content="微辣", created_at=1.0,
    )]
    assert svc.learn("u1", learned) == 1
    assert svc.export("u1")[0].content == "微辣"


# ------------------------------------------------------------------ maintain + privacy

def test_maintain_archives_stale(tmp_path):
    svc = service(tmp_path)
    old = svc.remember("u1", "陈年记录", MemoryKind.HISTORY, "food", now=0.0)
    result = svc.maintain("u1", now=86400.0 * 400)  # 13+ 个半衰期（30d）→ 强度 < 0.05
    stored = {r.id: r for r in svc.export("u1")}  # maintain 操作的是 store 内副本
    assert stored[old.id].status is MemoryStatus.ARCHIVED
    assert old.id in result["archived"]


def test_forget_single_and_all(tmp_path):
    svc = service(tmp_path)
    a = svc.remember("u1", "嗜辣", MemoryKind.PREFERENCE, "food", now=1.0)
    svc.remember("u1", "不吃香菜", MemoryKind.TABOO, "food", now=1.0)
    assert svc.forget("u1", a.id) == 1
    assert svc.forget("u1") == 1  # 全量删除（隐私合规）
    assert svc.export("u1") == []


# ------------------------------------------------------------------ migrations (P2-1)

def test_migration_initializes_meta(tmp_path):
    evolved = tmp_path / "evolved" / "users" / "u1"
    store = build_memory_store(tmp_path / "evolved", "u1")
    migrations.migrate(evolved)
    assert json.loads((evolved / "meta.json").read_text())["version"] == migrations.CURRENT_VERSION
    assert store.path.name == "memory.json"


def test_migration_registered_step_runs(tmp_path):
    ran = []
    migrations.register_step(1, lambda ctx: ran.append(ctx))
    migrations.CURRENT_VERSION = 2
    try:
        final = migrations.migrate(tmp_path, ctx="ctx1")
        assert final == 2 and ran == ["ctx1"]
        assert json.loads((tmp_path / "meta.json").read_text())["version"] == 2
    finally:
        migrations.CURRENT_VERSION = 1
        migrations.STEPS.pop(1, None)
