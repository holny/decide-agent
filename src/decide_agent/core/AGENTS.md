# src/decide_agent/core — Decision Business Logic (Pure, Fully Sync)

**Role:** Decision domain — no network I/O, no async. Network lives in `models/providers/*`; rules live in `experience/*` (both adapters/core extension allowed by lint).

## STRUCTURE

```
core/
├── decision/                  # DecisionEngine + provider chain (4 question shapes)
├── experience/                # see src/decide_agent/experience/ (separated subdir)
├── collect/                   # Capability registry + DAG scheduler (optional gather)
├── memory/                    # MemoryService (three-source facade, P2)
├── weights/                   # WeightModulation (planned P4)
├── workflow/                  # DecisionKernel + state machine + fingerprint + pending + budget
├── context/                   # ContextEngine (minimal sufficient context per stage)
└── narrator/                  # Output rendering (json_minimal/json_full/text)
```

**Concurrency model (§12.2):** `core/` is 100% synchronous. `async def`/`httpx`/`asyncio` are FORBIDDEN here. Async only in `channel/` and `app/` (HTTP/SSE/sweeper).

## WHERE TO LOOK

| Domain | Location | Notes |
|--------|----------|-------|
| 4 question shapes (classify/score/extract/verify) | `decision/engine.py` + `schemas/question.py` | Pure dispatcher to chain |
| Provider protocol | `decision/base.py::DecisionProvider` | Implementers: `experience/provider.py` + `models/providers/*` |
| Weighted synthesis (zero-model) | `decision/synthesis.py::synthesize` | L1 law |
| Confidence gating | `decision/gating.py::evaluate` | Uses `confidence_threshold` + `penalty_per_missing` |
| Sandbox flip detection | `decision/sandbox.py::potential_flip_dims` | Quick heuristic, ask-decision only |
| Workflow lifecycle | `workflow/kernel.py::DecisionKernel` | make_decision / respond / snapshot / restore |
| State machine | `workflow/state_machine.py` | created→collecting→[asking⇄collecting]→analyzing→[input-required]→suggested→completed |
| Pending registry | `workflow/pending.py` | Idempotent respond by `(decision_id, request_id)` |
| Stage fingerprint | `workflow/fingerprint.py` | Skip-stages-by-input-hash dedup |
| Clock budget | `workflow/budget.py::Budgets` | Per-stage timeouts → degrade, not fail |
| Capability registry | `collect/registry.py` | side=server/mcp/client/memory |
| DAG scheduler | `collect/scheduler.py` | Topological sort + max-parallel batches |
| Memory three-source merge | `memory/manager.py` + `memory/merge.py::SourceMerger` | external>session>user, conflict observe-confirm |
| YAML store | `memory/store.py::YAMLMemoryStore` | Path-isolated: `evolved/users/{owner}/memory.json` |
| Output rendering | `narrator/renderer.py::render` | 3 formats: json_minimal / json_full / text |

## CONVENTIONS

- **No network I/O in `core/`** — model calls live in `models/providers/*`. `core/decision/engine.py` dispatches via provider chain, which probes adapter availability.
- **All cross-module data = schemas/ pydantic** — never pass raw `dict` between modules.
- **Decisions are status-bivalent** — `DecisionOutcome.status` ∈ `{completed, require_action}`. `pending` field only when `require_action`.
- **Weights in config, not in prompts** — synthesis reads `weights_resolver(scene)` injected at boot.
- **`PendingRegistry` is the only mutable cross-call state** — keyed by `request_id` for idempotency.
- **`snapshot()` returns pure data** — persistence to `runtime/decisions/` is HTTP adapter's job (red line).

## ANTI-PATTERNS

- ❌ `async def` in core/ — concurrency is locked to sync (red line §12.2)
- ❌ Hard-fail on missing data — use `missing_information[]` (L4 law)
- ❌ Importing `channel/` or `models/` from core/ — layer direction is downward only
- ❌ Direct `os.environ` read — go through `config/loader.py` helpers
- ❌ Restoring score cache from snapshot — re-render produces same output, no stale result
- ❌ Vendor name in code (`typesafe_`, `jev_`, `laya_`) — only in `models/providers/` and config

## NOTES

- `_Session` in `workflow/kernel.py` keeps the per-decision mutable state — request, machine, fingerprint, candidates, score cache, ask_left, learned_memory. **In-process only**; HTTP adapter persists via `kernel.snapshot()`.
- `score_cache` is intentionally session-scoped — never restored on `restore()`, re-compute yields identical output (deterministic chain).
- `ExperienceProvider` lives in `experience/` (sibling package, not `core/`) but is allowed by lint to implement `core/decision/base.py`.
- Memory `raw_data_ref` for big POI/weather payloads — quota: 8KB/item, 32KB/total (configurable).
- `_learn_from_answer` only writes when `scope ∈ {session, always}` — `once` never persists (L6 scope-first).