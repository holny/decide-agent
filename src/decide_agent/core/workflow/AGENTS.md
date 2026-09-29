# src/decide_agent/core/workflow — Kernel State Machine

**Role:** `make_decision` lifecycle orchestrator: 意图 → 收集 → 判断 → 决策 → 呈现. Pure sync.

## STRUCTURE

```
workflow/
├── __init__.py                # exports DecisionKernel + helpers
├── kernel.py                  # ★ DecisionKernel: make_decision / respond / snapshot / restore
├── state_machine.py           # StateMachine: created→collecting→[asking⇄collecting]→analyzing→[input-required]→suggested→completed
├── fingerprint.py             # StageFingerprint: per-stage input hash → skip if unchanged
├── pending.py                 # PendingRegistry: PendingRequest lifecycle + idempotent respond
└── budget.py                  # Budgets: per-stage clock (collect/decide/analysis)
```

## WHERE TO LOOK

| Need | File | Notes |
|----|------|-------|
| Change make_decision flow | `kernel.py::DecisionKernel.make_decision` | Phases ① intent → ② collect → ③ decide (analyze) → ④ gate/ask → ⑤ render |
| Change state transitions | `state_machine.py::StateMachine` | All transitions guarded; reverting raises |
| Add a stage-level skip | `fingerprint.py::StageFingerprint.should_skip(stage, payload)` | Input-hash based; re-render produces same result |
| Handle respond lifecycle | `kernel.py::DecisionKernel.respond` | Idempotent by `(decision_id, request_id)` via `_replay` dict |
| Configure time budgets | `budget.py::Budgets(collect_s, decide_s, analysis_s)` | None = unlimited; expired → neutral 0.5 + caveat |
| Sweep expired pendings | `kernel.py::sweep(now)` | Called by channel sweeper; returns expired request_ids |
| Restore after restart | `kernel.py::restore(decision_id, snap)` | HTTP adapter passes `kernel` block from snapshot |
| Snapshot a decision | `kernel.py::snapshot(decision_id)` | Pure data; persistence to `runtime/` is HTTP adapter's job |

## CONVENTIONS

- **`_Session` is per-decision in-process state** — carries request, state machine, fingerprint, candidates, score cache, ask_left, learned memory. Not thread-safe (one decision per session).
- **`respond()` is fully idempotent** — same `(decision_id, request_id)` replays original outcome from `_replay` dict. Duplicate → same result.
- **Pending kind bivalent**: `tool | question`. Tool kind carries `payload.outputSchema`; question kind carries `text/options/allow_free_text/scope_choices`.
- **Stage fingerprint payload = JSON-serializable dict** — compute hash, skip if same.
- **Budget expiry = neutral + disclosed, not exception** — `disclosed.append("决策时钟预算耗尽...")` + dimension added to `missing`.
- **Snapshot excludes score cache** — `_hashes` are stored, not values. Re-render re-computes same result.

## ANTI-PATTERNS

- ❌ `async def` in workflow — fully sync (red line §12.2)
- ❌ Persisting from core — `snapshot()` returns data, channel layer writes
- ❌ Restoring score cache — re-render produces identical output, no need
- ❌ Reversing a state transition — StateMachine guards direction
- ❌ Auto-switching `decision_mode` based on metrics — recommend only (red line #9)
- ❌ Direct `os.environ` reads — go through `config/loader.py`

## NOTES

- **Two ask-bid phases** (A/B 拍): A拍 fires inside `make_decision` if collect has a hard gap; B拍 fires inside `_analyze` if confidence gate fails OR flip detected. Both decrement `ask_left` by 1.
- **`scope_choices = [once, session, always]`** for question kind — once only affects this decision; session writes `MemoryScope.SESSION`; always writes `MemoryScope.USER` (P2).
- **`expired → `PendingExpiredError`** — HTTP adapter maps to 409 with DecisionError body (red line #13).
- `_block_to_clarify` is the **only hard-ending path** — empty candidates + `interactive=True` → require_action with clarify question; otherwise → completed with confidence 0.0.
- Per-decision `_replay` dict means memory grows with active pendings; channel sweeper must `kernel.accept()` or `expire_due()` to clean up.