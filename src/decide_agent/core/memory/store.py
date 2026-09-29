"""MemoryStore: storage interface + JSON implementation（人类可读可编辑）.

存储即数据（项目哲学：场景/规则/记忆全 JSON/JSONC）。文件 = evolved/users/{owner}/memory.json
（机器写纯 JSON；注释类静态配置走 .jsonc），用户可直接查看编辑；
写入走 原子替换（tmp+rename）+线程锁。
查询强制 owner 过滤（三层隔离之查询层，ARCHITECTURE §6）。
接口保留：未来真需要语义检索/海量记忆时再加索引实现（当前规模不需要）。
"""
import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from decide_agent.schemas.memory import MemoryRecord, MemoryStatus

_FILE = "memory.json"


class MemoryStore(ABC):
    @abstractmethod
    def upsert(self, record: MemoryRecord) -> None: ...

    @abstractmethod
    def get(self, owner_id: str, record_id: str) -> MemoryRecord | None: ...

    @abstractmethod
    def search(
        self,
        owner_id: str,
        scene: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        statuses: tuple | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]: ...

    @abstractmethod
    def delete(self, owner_id: str, record_id: str) -> bool: ...

    @abstractmethod
    def list_all(self, owner_id: str, statuses: tuple | None = None) -> list[MemoryRecord]: ...

    @abstractmethod
    def count(self, owner_id: str) -> int: ...


class JsonMemoryStore(MemoryStore):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """memory.json 位置（用户可直接打开编辑）。"""
        return self._path

    # ------------------------------------------------------------------ io

    def _load(self) -> list[MemoryRecord]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text("utf-8")) or {}
        return [MemoryRecord.model_validate(item) for item in data.get("records", [])]

    def _save(self, records: list[MemoryRecord]) -> None:
        """原子替换写：tmp + os.replace（并发/崩溃安全）。"""
        payload = {"records": [r.model_dump(mode="json") for r in records]}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
        os.replace(tmp, self._path)

    # ------------------------------------------------------------------ MemoryStore

    def upsert(self, record: MemoryRecord) -> None:
        with self._lock:
            records = self._load()
            for index, existing in enumerate(records):
                if existing.id == record.id:
                    records[index] = record
                    break
            else:
                records.append(record)
            self._save(records)

    def get(self, owner_id: str, record_id: str) -> MemoryRecord | None:
        with self._lock:
            for record in self._load():
                if record.id == record_id and record.owner_id == owner_id:
                    return record
        return None

    def search(
        self,
        owner_id: str,
        scene: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        statuses: tuple | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        statuses = statuses or (MemoryStatus.ACTIVE.value,)
        with self._lock:
            matches = [
                record for record in self._load()
                if record.owner_id == owner_id
                and record.status.value in statuses
                and (scene is None or record.scene in (scene, "global"))
                and (kind is None or record.kind.value == kind)
                and (query is None or query in record.content)
            ]
        matches.sort(key=lambda r: (r.strength, r.last_used_at or 0.0), reverse=True)
        return matches[:limit]

    def delete(self, owner_id: str, record_id: str) -> bool:
        with self._lock:
            records = self._load()
            kept = [r for r in records if not (r.id == record_id and r.owner_id == owner_id)]
            if len(kept) == len(records):
                return False
            self._save(kept)
            return True

    def list_all(self, owner_id: str, statuses: tuple | None = None) -> list[MemoryRecord]:
        statuses = statuses or tuple(s.value for s in MemoryStatus)
        with self._lock:
            return [
                r for r in self._load()
                if r.owner_id == owner_id and r.status.value in statuses
            ]

    def count(self, owner_id: str) -> int:
        with self._lock:
            return sum(1 for r in self._load() if r.owner_id == owner_id)


def new_record_id() -> str:
    import uuid

    return f"m-{uuid.uuid4().hex[:12]}"
