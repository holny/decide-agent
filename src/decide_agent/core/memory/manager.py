"""MemoryService: three-source facade — remember/recall/ingest/learn/export/forget.

- scope=user 落长期（SQLite, evolved/users/{owner}/memory/）；ephemeral 即用即弃不落库
- 低置信挂起（status=PENDING）；矛盾冲突观察制（merge.py）
- external 限额：单份 8KB / 总量 32KB（可配），超限卸载 raw/ + caveat（P2-3）
- learned_memory 双向（P2-5）：给宿主入库 + 按 scope 落自有层
"""
from pathlib import Path

from decide_agent.common.ids import new_request_id
from decide_agent.core.memory import policy
from decide_agent.core.memory.merge import MergeReport, SourceMerger
from decide_agent.core.memory.store import JsonMemoryStore, MemoryStore, new_record_id
from decide_agent.schemas.memory import MemoryKind, MemoryRecord, MemoryScope, MemoryStatus

PER_ITEM_LIMIT = 8 * 1024
TOTAL_LIMIT = 32 * 1024
LOW_CONFIDENCE_THRESHOLD = 0.6


def build_memory_store(evolved_dir: Path, owner_id: str) -> JsonMemoryStore:
    """owner 分区：evolved/users/{owner}/memory.json（人类可读可编辑，P2-6）。"""
    return JsonMemoryStore(Path(evolved_dir) / "users" / owner_id / "memory.json")  # meta 版本门由文件头 version 承接


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        raw_dir: Path | None = None,
        *,
        per_item_limit: int = PER_ITEM_LIMIT,
        total_limit: int = TOTAL_LIMIT,
        merger: SourceMerger | None = None,
    ) -> None:
        self._store = store
        self._raw_dir = Path(raw_dir) if raw_dir else None
        self._per_item = per_item_limit
        self._total = total_limit
        self._session_records: dict[str, list[MemoryRecord]] = {}
        self._merger = merger or SourceMerger()

    # ------------------------------------------------------------------ write

    def remember(
        self,
        owner_id: str,
        content: str,
        kind: MemoryKind,
        scene: str = "global",
        *,
        scope: MemoryScope | str = MemoryScope.USER,
        source: str = "declare",
        confidence: float = 0.6,
        now: float,
    ) -> MemoryRecord | None:
        """写入一条记忆。ephemeral 不落库；低置信挂起；高置信 ACTIVE。"""
        record = MemoryRecord(
            id=new_record_id(),
            owner_id=owner_id,
            scope=MemoryScope(scope),
            kind=kind,
            scene=scene,
            content=content,
            confidence=confidence,
            created_at=now,
            source=source,
            status=MemoryStatus.PENDING if confidence < LOW_CONFIDENCE_THRESHOLD else MemoryStatus.ACTIVE,
        )
        if record.scope is MemoryScope.EPHEMERAL:
            return record  # 即用即弃
        self._store.upsert(record)
        return record

    def supersede_with(self, owner_id: str, old: MemoryRecord, content: str, now: float, **kwargs) -> MemoryRecord:
        """更新语义：旧条目 superseded，新条目 ACTIVE。"""
        replacement = self.remember(owner_id, content, old.kind, old.scene, now=now, **kwargs)
        policy.supersede(old, replacement.id)
        self._store.upsert(old)
        return replacement

    def confirm_pending(self, owner_id: str, record_id: str, new_content: str | None = None) -> MemoryRecord | None:
        record = self._store.get(owner_id, record_id)
        if record is None:
            return None
        policy.promote_on_confirm(record, new_content)
        self._store.upsert(record)
        return record

    # ------------------------------------------------------------------ read

    def recall(
        self,
        owner_id: str,
        scene: str | None = None,
        query: str | None = None,
        *,
        external: list[MemoryRecord] | None = None,
        now: float,
    ) -> MergeReport:
        """三源合并生效视图（external > session > user）+ 冲突观察。"""
        user_records = self._store.search(owner_id, scene=scene, query=query)
        session_records = [
            r for records in self._session_records.values() for r in records
            if scene is None or r.scene in (scene, "global")
        ]
        return self._merger.merge(user_records, session_records, list(external or []), now)

    # ------------------------------------------------------------------ external 限额（P2-3）

    def ingest_external(self, items: list) -> tuple[list[dict], list[str]]:
        """宿主注入：限额校验（单份/总量），超限卸载 raw/ + caveat。返回 (接受项, caveats)."""
        accepted: list[dict] = []
        caveats: list[str] = []
        total = 0
        for index, item in enumerate(items):
            size = len(str(item).encode("utf-8"))
            if size > self._per_item:
                ref = self._offload(item, index)
                accepted.append({"raw_data_ref": ref})
                caveats.append(f"external[{index}] 单份超限（{size}B>{self._per_item}B），已卸载 raw/")
                continue
            if total + size > self._total:
                caveats.append(f"external[{index}] 超总量限额（{self._total}B），本次不注入")
                continue
            total += size
            accepted.append(item if isinstance(item, dict) else {"content": str(item)})
        return accepted, caveats

    def _offload(self, item, index: int) -> str:
        if self._raw_dir is None:
            return f"external://dropped[{index}]"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        path = self._raw_dir / f"external_{new_request_id()}.json"
        path.write_text(str(item), "utf-8")
        return str(path)

    # ------------------------------------------------------------------ learned（P2-5）

    def learn(self, owner_id: str, entries: list[MemoryRecord]) -> int:
        """learned_memory[] 双向：落自有层（宿主侧入库由宿主执行）。"""
        count = 0
        for entry in entries:
            record = MemoryRecord.model_validate({
                **entry.model_dump(mode="json"), "id": new_record_id(), "owner_id": owner_id,
            })
            if record.scope is not MemoryScope.EPHEMERAL:
                self._store.upsert(record)
            count += 1
        return count

    # ------------------------------------------------------------------ 保鲜/淘汰（P2-4）

    def maintain(self, owner_id: str, *, keep: int = 200, now: float) -> dict:
        records = self._store.list_all(owner_id)
        for record in records:
            record.strength = policy.decayed_strength(record, now)
            if record.status is MemoryStatus.ACTIVE:
                self._store.upsert(record)
        archived = policy.sweep_archival(records, now) + policy.evict_lru(records, keep, now)
        for record in records:
            if record.status is MemoryStatus.ARCHIVED:
                self._store.upsert(record)
        return {"archived": archived, "scanned": len(records)}

    # ------------------------------------------------------------------ privacy（P2-6）

    def export(self, owner_id: str) -> list[MemoryRecord]:
        """GET /v1/memory/{owner_id} 数据源：该 owner 全量。"""
        return self._store.list_all(owner_id)

    def forget(self, owner_id: str, record_id: str | None = None) -> int:
        """DELETE /v1/memory/{owner_id}：单条或全量（隐私合规）。"""
        if record_id is not None:
            return int(self._store.delete(owner_id, record_id))
        return sum(self._store.delete(owner_id, r.id) for r in self._store.list_all(owner_id))
