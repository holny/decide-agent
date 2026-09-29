# src/decide_agent/app — Assembly Layer

**Role:** Wire everything. **Only all-knowing module = `bootstrap.py`** (L3 + §12.2 red line).

## STRUCTURE

```
app/
├── __init__.py                # empty
├── bootstrap.py               # ★ ONLY all-knowing DI — every concrete class wired exactly here
└── entry.py                   # ★ DecideApp — channel's only legal surface
```

## WHERE TO LOOK

| Task | Look here |
|------|-----------|
| Add a new provider tier | `bootstrap.py::build_provider_chain` — also implement `core/decision/base.py` |
| Change config layout | `bootstrap.py` reads via `config/loader.py` helpers; do NOT read `os.environ` directly |
| Wire a new channel | Add to `bootstrap.py` (NO new concrete class in app/ other than wiring) |
| Add `DecideApp` capability | `entry.py` — channels reach kernel via `decide.make_decision/respond/sweep/render` |
| Change ownership default | `bootstrap.py::LOCAL_OWNER = "local"` (P3+ integrates multi-tenant auth) |

## CONVENTIONS

- **DO NOT add new concrete classes** in `app/` other than wiring in `bootstrap.py` — this is a hard rule (red line §12.2).
- `build_kernel(...)` accepts overrides for every component — tests pass `data_dir=tmp_path`, custom `engine`, custom `memory`, etc.
- `bootstrap.py` imports go to: `config/loader`, `config/paths`, `core/{collect,decision,memory,workflow}`, `experience/provider`, `models/providers/*`, `plugins/discovery`, `schemas/collect`. **No upward** imports.
- `entry.py` re-exports `DecideApp`/`build_app` from `bootstrap.py`; channels import `build_app` and stop there.

## ANTI-PATTERNS

- ❌ Adding `MyService` class in `app/` — wire in `bootstrap.py` instead
- ❌ Reading `os.environ` directly — go through `config/loader.py`
- ❌ Importing `core/*` internals from `entry.py` — only `kernel`, `narrator`, `workflow.schemas` are public surface
- ❌ Calling `kernel._engine` from `entry.py` — exposes private API, breaks encapsulation contract

## NOTES

- `bootstrap.py::build_kernel` is the canonical production assembly; tests inject by passing args.
- `build_app(data_dir)` is the universal entry point for ALL channels.
- Channel adapters must call `build_app()` not `build_kernel()` — channel-level wiring lives in `bootstrap.py`.
- `DecideApp.render(outcome, fmt)` delegates to `core.narrator.render` and applies scene `dimension_display` mapping.