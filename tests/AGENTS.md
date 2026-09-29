# tests/ — Test Suite

**Role:** Unit + integration + e2e tests. `asyncio_mode=auto` — async tests run without explicit `@pytest.mark.asyncio`.

## STRUCTURE

```
tests/
├── __init__.py                # empty
├── conftest.py                # (not currently present; see "Notes")
├── fixtures/
│   ├── __init__.py
│   └── providers.py           # deterministic stub providers for tests
├── unit/                      # mirrors src/decide_agent/ structure
│   ├── channel/               # events tests
│   ├── common/                # empty __init__
│   ├── config/                # test_config.py, test_p1_config.py
│   ├── core/
│   │   ├── collect/           # test_registry.py, test_scheduler.py
│   │   ├── context/           # test_engine.py
│   │   ├── decision/          # test_chain, test_engine, test_gating, test_sandbox, test_synthesis
│   │   ├── memory/            # test_manager, test_merge_policy, test_store
│   │   ├── narrator/          # test_renderer
│   │   └── workflow/          # test_budget, test_fingerprint, test_kernel, test_pending, test_snapshot, test_state_machine
│   ├── decision/              # (legacy) test_engine
│   ├── experience/            # test_converge, test_provider, test_recorder, test_rules_loader
│   ├── models/                # test_transports
│   ├── orchestrator/           # (legacy) test_state_machine
│   ├── plugins/               # test_discovery
│   ├── schemas/               # test_error
│   └── skills/                # test_registry
├── integration/
│   ├── __init__.py
│   ├── test_a2a_api.py
│   ├── test_collect_food.py
│   ├── test_experience_chain.py
│   ├── test_http_api.py
│   ├── test_mcp_stdio.py
│   ├── test_memory_kernel.py
│   └── test_packaging.py
├── e2e/
│   ├── __init__.py
│   ├── test_food_loop.py
│   └── test_p1_demo.py
└── fixtures/
```

## WHERE TO LOOK

| Test category | Pattern | Notes |
|---------------|---------|-------|
| Pure unit | `tests/unit/core/{module}/test_*.py` | Sync, no fixtures beyond defaults |
| Provider stub | `tests/fixtures/providers.py` | Deterministic answer for chain tests |
| Integration | `tests/integration/test_*.py` | Boots full kernel, uses `tmp_path` for data_dir |
| E2E | `tests/e2e/test_*.py` | Drives CLI / HTTP end-to-end |
| Config isolation | use `tmp_path` + custom `user_config_path` arg in `load_config` | May need `load_config.cache_clear()` |
| Owner isolation | inject `owner_id="local"` and `tmp_path` as `data_dir` | Default `LOCAL_OWNER` from bootstrap |

## CONVENTIONS

- **`asyncio_mode = "auto"`** in `pyproject.toml` — async tests run without explicit marker.
- **Mirrors `src/decide_agent/` structure** — `tests/unit/core/decision/` covers `src/decide_agent/core/decision/`.
- **Sync core tests are pure** — no asyncio, no httpx. Network-touching code lives in `tests/integration/` (httpx-mocked or Live).
- **No `conftest.py`** currently — fixtures are flat in `tests/fixtures/` or inline. Add one when shared fixtures emerge.
- **Inject `data_dir=tmp_path`** for any test that touches persistence (memory, snapshots, sweepers).
- **Use `build_kernel(...)` overrides** to bypass `bootstrap.build_app()` — pass custom `engine`, `memory`, etc.
- **Provider stub** returns deterministic `TypedAnswer` — enables chain-degradation tests without network.

## ANTI-PATTERNS

- ❌ Mocking at import level — inject stubs via constructor params
- ❌ Using real API keys in tests — always stub or env-mock
- ❌ Sharing MemoryService across owners in one test — use per-test fixtures
- ❌ Testing against real network in `unit/` — move to `integration/`
- ❌ Hardcoding `/tmp/decide-agent-test` paths — use `tmp_path` fixture
- ❌ Skipping test cleanup — `tmp_path` is auto-cleaned, but explicit shutdown for sweepers/HTTP servers

## NOTES

- **Run all**: `uv run pytest` (53+ tests).
- **Run module**: `uv run pytest tests/unit/core/workflow/`.
- **Run by name**: `uv run pytest -k test_kernel`.
- **Coverage**: not enforced. Pytest runs all discovered tests; CI gate is `ruff + import-linter + pytest exit 0`.
- **`test_p1_demo.py`** is the golden-case parity test — same input as P0, must produce equivalent DecisionReport (per TECHNICAL_PLAN.md P1-10).
- **`test_packaging.py`** verifies wheel includes `plugins/` + `skills/` via `share/decide_agent/` force-include.
- **`test_http_api.py`** uses FastAPI's `TestClient` (sync) — no live server needed.
- **`test_mcp_stdio.py`** runs MCP server in a subprocess and exchanges JSON-RPC messages.
- **No `tests/__init__.py` mutations** — keep empty; pytest handles rootdir.
- **`@pytest.fixture` scope**: default `function`. Use `module` or `session` only for expensive setup (e.g., loaded SkillRegistry).