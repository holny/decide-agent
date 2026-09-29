# src/decide_agent/schemas — Cross-Module Pydantic Contracts

**Role:** Cross-module language. **Only pydantic models** cross module boundaries. No business logic.

## STRUCTURE

```
schemas/
├── __init__.py                # Re-exports
├── candidate.py               # Candidate (id, name, attrs)
├── decision.py                # DecisionResult, DimensionScore, Recommendation (output type)
├── workflow.py                # DecisionRequest, DecisionOutcome, PendingRequest, PendingKind
├── question.py                # TypedQuestion, TypedAnswer, QuestionShape enum, Question
├── memory.py                  # MemoryRecord, MemoryEntry, MemoryKind, MemoryScope, MemoryStatus
├── collect.py                 # Capability, Side, CollectorOutcome
├── evidence.py                # Evidence
├── judgment.py                # JudgmentRecord (chain logging)
├── error.py                   # DecisionError, ErrorSeverity, ErrorSource (P1-1)
└── events.py                  # EventType enum + payload models
```

## WHERE TO LOOK

| Need | File | Notes |
|----|------|-------|
| Decision report | `decision.py::DecisionResult` + `Recommendation` + `DimensionScore` | Output of completed decisions |
| Pending request | `workflow.py::PendingRequest` + `PendingKind` (`tool | question`) | Output of `require_action` |
| Decision outcome | `workflow.py::DecisionOutcome` | Status-bivalent: `completed | require_action` |
| Decision request | `workflow.py::DecisionRequest` | Input: `question, scene_hint, candidates, tools, config, memory, interactive, ask_budget, ...` |
| Question shape | `question.py::QuestionShape` enum | `classify | score | extract | verify` |
| Typed question | `question.py::TypedQuestion` | shape, scene, dimension, text, slot, context |
| Typed answer | `question.py::TypedAnswer` | shape, value, distribution, confidence, basis, provider, degraded |
| Memory record | `memory.py::MemoryRecord` | id, owner_id, scope, kind, scene, content, confidence, strength, half_life_days, status |
| Capability | `collect.py::Capability` | name, side, description |
| Collector outcome | `collect.py::CollectorOutcome` | candidates, env, needs, disclosed |
| Error | `error.py::DecisionError` | code, severity, source, degrade_path (P1-1) |
| Event payload | `events.py::EventType` enum | completed, expired, pending_request, stage |

## CONVENTIONS

- **Pydantic only, no business logic** — schemas define shape and validation, methods are properties or model_validators only.
- **All cross-module data uses schemas** — never pass raw `dict` between modules. Lint enforces via type hints.
- **Status-bivalent outcomes** — `DecisionOutcome.status` ∈ `{completed, require_action}`. `pending` field only when `require_action`; `result` field only when `completed`.
- **`degraded=true` for partial answers** — `TypedAnswer.degraded` marks confidence-impacting gaps.
- **`confidence: float` always present** — provider must return it; missing → `RuntimeError` (SystemOneAdapter enforces).
- **Memory scopes**: `user | session | ephemeral`. Only `user` writes to SQLite (P2).
- **`ErrorSeverity`** — typed classification; `ErrorSource` — module attribution.

## ANTI-PATTERNS

- ❌ Adding methods with I/O to schema classes — validation only
- ❌ Passing raw `dict` between modules — always schemas
- ❌ Importing from `core/*` in `schemas/` — schemas are the foundation, no upward deps
- ❌ Adding a third outcome status — `completed | require_action` only (no "pending" half-state at outcome level)
- ❌ Storing mutable internal state in a Schema — use `core/*` for that

## NOTES

- **`DecisionRequest.model_fields`** is used by HTTP adapter to filter unknown fields (`body.items() if key in DecisionRequest.model_fields`).
- **`TypedQuestion.model_dump(mode="json")`** used by SystemOneAdapter to serialize payload for HTTP POST.
- **`PendingRequest.payload`** is a free-form `dict` carrying shape-specific data: `{dimension, question, scope_choices}` for questions; `{tool, input, output_schema}` for tools.
- **`DecisionOutcome.fingerprint_skips[]`** discloses which stages were skipped due to fingerprint match.
- **`MemoryStatus = active | pending | superseded | archived`** — `pending` means low-confidence, awaiting confirmation.
- **`Side = server | mcp | client | memory`** — capability side enum used for priority ordering in collect scheduler.
- **`QuestionScope = once | session | always`** — set by user when answering a question; kernel uses to decide memory persistence.