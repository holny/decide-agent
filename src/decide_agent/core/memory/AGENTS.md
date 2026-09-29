# src/decide_agent/core/memory — Three-Source Memory Facade

**Role:** MemoryService facade for `user_memory` / `session_memory` / `external_memory`. SQLite-backed store with owner isolation.

## STRUCTURE

```
memory/
├── __init__.py
├── manager.py                 # ★ MemoryService: remember/recall/ingest/learn/export/forget
├── store.py                   # MemoryStore Protocol + YAMLMemoryStore（memory.json，人类可编辑）
├── merge.py                   # SourceMerger: external > session > user, observe-confirm conflicts
└── policy.py                  # freshness/strength/half_life/eviction/LRU rules
```

## WHERE TO LOOK

| Need | File | Notes |
|----|------|-------|
| Remember something | `manager.py::MemoryService.remember(owner, content, kind, scene, scope, ...)` | scope=user → SQLite; scope=session → in-mem dict |
| Recall by query | `manager.py::MemoryService.recall(owner, scene, kind, query)` | P2-1 returns `MemoryRecord` list |
| Merge three sources | `manager.py::MemoryService.ingest(owner, external, session, user)` | `external > session > user` priority |
| Force-write from feedback | `manager.py::MemoryService.learn(owner, content, scene, source)` | Bypasses low-confidence gate |
| Export for privacy compliance | `manager.py::MemoryService.export(owner)` | Used by `GET /v1/memory/{owner_id}` |
| Forget (delete) | `manager.py::MemoryService.forget(owner, record_id=None)` | record_id=None → delete all for owner |
| Persist to YAML | `store.py::YAMLMemoryStore` | Path-isolated: `evolved/users/{owner}/memory.json` |
| Search records | `store.py::YAMLMemoryStore.search(owner, scene, kind, query)` | 内存过滤；语义检索需要时再加索引实现 |
| Run schema migrations | `common/migrations.py::migrate(owner_dir)` | meta.json version gate |
| Owner-path isolation | `manager.py::build_memory_store(evolved_dir, owner_id)` | `users/{owner}/memory.json` |

## CONVENTIONS

- **Three-source priority (hard-coded)**: `external > session > user`. External wins because it carries user's "now" intent; user is "long-term preference" baseline.
- **Conflict = observe, don't overwrite** — same key + different content seen multiple times → only then update (anti-spam). One-off conflicting memory gets marked PENDING.
- **`scope=user` only when confidence ≥ LOW_CONFIDENCE_THRESHOLD (0.6)** — else PENDING (pending user confirmation).
- **Path isolation by owner** — every read/write filtered by `owner_id`; never leak cross-owner.
- **Quota enforcement**: `per_item_limit = 8KB`, `total_limit = 32KB` (configurable). Overflow → unload to `raw/` + add caveat.
- **`evolved/` is the only write dir** for memory — `runtime/` is volatile (cache, raw, snapshots); `user/` is user-owned config (agent does NOT write).

## ANTI-PATTERNS

- ❌ Reading cross-owner data — always filter by `owner_id` parameter
- ❌ Auto-promoting to `scope=user` without confidence check
- ❌ Silent overwrite on conflict — observe-confirm protocol (red line #12)
- ❌ Storing secrets in memory — preference/taboo/history/fact only
- ❌ Putting memory writes in `runtime/` — evolved/ is the only durable write zone

## NOTES

- `MemoryScope` = `{user, session, ephemeral}`. `ephemeral` is in-process only, never persisted.
- `MemoryKind` = `{preference, taboo, history, fact}` — used to filter in `recall` and merge.
- `MemoryStatus` = `{active, pending, superseded, archived}` — `pending` waits for confirmation; `superseded` keeps history; `archived` is purge-eligible.
- Half-life: taste ~180d, budget ~60d — `strength` decays exponentially, LRU eviction kicks in below threshold.
- `Learn from answer` (in `workflow/kernel.py::_learn_from_answer`) writes only when `scope ∈ {session, always}` — `once` never persists (L6 scope-first).
- HTTP adapter wraps `MemoryService` per-request owner via `memory_for(owner_id)` — never shares a service across owners.