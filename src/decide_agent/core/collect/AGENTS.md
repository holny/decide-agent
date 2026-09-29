# src/decide_agent/core/collect — Capability Registry + DAG Scheduler

**Role:** Optional gather phase. Decide what info to fetch before deciding. Pure code + judgment-out-of-loop.

## STRUCTURE

```
collect/
├── __init__.py
├── collector.py               # TemplateCollector: orchestrator (combines scheduler + scene loader)
├── scheduler.py               # CollectScheduler: DAG top-sort + max-parallel batches + degrade policies
├── registry.py                # CapabilityRegistry: register capabilities by name+side
└── judge_port.py              # JudgePort: inject extract/verify calls OUT to DecisionEngine (no upward import)
```

## WHERE TO LOOK

| Need | File | Notes |
|----|------|-------|
| Register a new capability | `registry.py::CapabilityRegistry.register(Capability)` | `side` ∈ `{server, mcp, client, memory}` |
| Trigger collection | `collector.py::TemplateCollector.collect(scene, request)` | Returns `CollectorOutcome{candidates, env, needs, disclosed}` |
| Decide whether to skip | `collector.py` — caller pre-provides candidates → skip; stage fingerprint unchanged → skip; scene=general → skip | Three skip conditions |
| Define a DAG | `skills/<scene>/info_needs.jsonc` — `collect[{capability, degrade}]` + `dependencies{}` | YAML only, no Python |
| Apply degrade policies | `scheduler.py::CollectScheduler._apply_degrade` | `ask_once | degrade | neutral | block_to_clarify` |
| Get extract/verify judgment | `judge_port.py::JudgePort` | Injected by kernel; collect doesn't import core.decision |
| Map dimension → capability | `skills/<scene>/dimensions.jsonc::source` field | e.g. `source: geo`, `source: weather`, `source: ask_user` |

## CONVENTIONS

- **`side` priority order** (registered first = highest priority): `client > server > mcp > memory`. User's GPS beats IP-based geo, etc.
- **Capability name is the contract** — tool plugin name = capability name. Plugin loader (`plugins/discovery.py`) feeds registry at boot.
- **Degrade policies**: `ask_once` (single retry with human), `degrade` (weight zero + renormalize + confidence cut), `neutral` (0.5 + caveat), `block_to_clarify` (only hard end — empty candidates → require_action).
- **`judge_port` for any "smart" decision** — collect scheduler is zero-intelligence. All extract/verify go through injected JudgePort (which wraps DecisionEngine).
- **Failure attribution** — failed leaf propagates to root via DAG; root node decides degrade policy.
- **`dependencies{}` in info_needs.jsonc** — DAG topology; missing dep = topological sort fails = scene error.

## ANTI-PATTERNS

- ❌ Importing `core.decision.*` from `collect/` — use `judge_port.py` injection (no upward import)
- ❌ Adding LLM/heuristic for collect orchestration — pure DAG scheduling only
- ❌ Blocking on missing data — apply degrade policy, never raise to caller (L4)
- ❌ Putting tool implementation in `collect/` — only registration; impls live in `plugins/tools/*/impl.py`
- ❌ Long-lived registry entries — capabilities are loaded fresh at boot

## NOTES

- `Capability.side` Schema comes from `schemas/collect.py::Side` enum.
- `CollectorOutcome` includes `candidates`, `env`, `needs`, `disclosed` — kernel merges `env` into `request.env`; `needs` triggers A拍 ask if hard gap.
- `block_to_clarify` is the **only hard-ending path** in collect — empty candidates + interactive → require_action with clarify question (handled by kernel._block_to_clarify).
- **Capability discovery chain** mirrors skill lookup: `builtin < user < project` (`config/paths.py::builtin_share_dirs` returns plugins dir).
- `disclosed[]` accumulates gaps surfaced during collection — surfaced to user via `missing_information[]`.
- Plugins are discovered at boot via `plugins/discovery.py::discover_tools` and registered into `CapabilityRegistry` by `bootstrap.py::build_collector`.