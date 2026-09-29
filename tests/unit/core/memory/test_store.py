"""JsonMemoryStore tests: CRUD, search, owner isolation, 人类可读可编辑（P2 存储定稿）。"""
import json

from decide_agent.core.memory.store import JsonMemoryStore
from decide_agent.schemas.memory import MemoryKind, MemoryRecord, MemoryScope, MemoryStatus


def rec(rid="m1", owner="u1", content="嗜辣", kind=MemoryKind.PREFERENCE, scene="food",
        status=MemoryStatus.ACTIVE) -> MemoryRecord:
    return MemoryRecord(
        id=rid, owner_id=owner, scope=MemoryScope.USER, kind=kind, scene=scene,
        content=content, created_at=100.0, status=status,
    )


def store(tmp_path, owner="u1") -> JsonMemoryStore:
    return JsonMemoryStore(tmp_path / owner / "memory.json")


def test_crud_roundtrip(tmp_path):
    s = store(tmp_path)
    s.upsert(rec())
    loaded = s.get("u1", "m1")
    assert loaded is not None and loaded.content == "嗜辣" and loaded.kind is MemoryKind.PREFERENCE
    assert s.count("u1") == 1
    assert s.delete("u1", "m1") and not s.delete("u1", "m1")
    assert s.get("u1", "m1") is None


def test_search_filters(tmp_path):
    s = store(tmp_path)
    s.upsert(rec("m1", content="嗜辣"))
    s.upsert(rec("m2", content="不吃香菜", kind=MemoryKind.TABOO))
    s.upsert(rec("m3", content="预算50-80", kind=MemoryKind.FACT, scene="global"))
    assert [r.id for r in s.search("u1", scene="food")] == ["m1", "m2", "m3"]  # scene+global 兜底
    assert [r.id for r in s.search("u1", scene="travel")] == ["m3"]  # global 全场景生效
    assert [r.id for r in s.search("u1", kind=MemoryKind.TABOO)] == ["m2"]
    assert [r.id for r in s.search("u1", query="辣")] == ["m1"]
    assert [r.id for r in s.search("u1", statuses=(MemoryStatus.ARCHIVED,))] == []


def test_owner_isolation(tmp_path):
    """三层隔离：查询强制 owner 过滤 + 路径前缀各一份文件。"""
    s1 = store(tmp_path, "u1")
    s2 = store(tmp_path, "u2")
    s1.upsert(rec("m1", owner="u1"))
    assert s2.get("u2", "m1") is None
    assert s2.search("u2") == []
    assert s2.count("u2") == 0
    assert s2.delete("u2", "m1") is False


def test_file_is_human_readable_and_editable(tmp_path):
    """存储定稿核心诉求：memory.json 用户可直接查看/编辑。"""
    s = store(tmp_path)
    s.upsert(rec("m1", content="嗜辣"))
    raw = (tmp_path / "u1" / "memory.json").read_text("utf-8")
    assert "嗜辣" in raw and '"kind": "preference"' in raw  # 无需工具即可读

    # 用户手动编辑（改内容）→ 存储原样接受（编辑即合入的载体）
    data = json.loads(raw)
    data["records"][0]["content"] = "改吃清淡"
    (tmp_path / "u1" / "memory.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    assert s.get("u1", "m1").content == "改吃清淡"


def test_upsert_edits_manual_file(tmp_path):
    """upsert 保留用户手改的其它字段（合并语义：按 id 覆盖对应条目）。"""
    s = store(tmp_path)
    s.upsert(rec("m1"))
    s.upsert(rec("m2", content="不吃香菜", kind=MemoryKind.TABOO))
    s.upsert(rec("m1", content="微辣"))  # 覆盖 m1，m2 不动
    assert s.get("u1", "m1").content == "微辣"
    assert s.get("u1", "m2").content == "不吃香菜"
