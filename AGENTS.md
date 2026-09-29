# PROJECT KNOWLEDGE BASE — decide-agent

**Generated:** 2026-09-24
**Commit:** fb40316 (Initial commit)
**Branch:** main
**Python:** ≥3.11 | **Build:** hatchling | **Pkg mgr:** uv | **Lint:** ruff | **Test:** pytest+pytest-asyncio

## OVERVIEW

Intelligent decision agent for indecisive users. Single call = single decision workflow: **意图 → 收集 → 判断 → 决策 → 呈现**, returns standard JSON `DecisionReport`. Four surface forms (MCP / HTTP+SSE / CLI / A2A), one sync kernel. Architecture deeply specified in `ARCHITECTURE.md` v4 / `docs/TECHNICAL_PLAN.md`.

## STRUCTURE (three-tier repository)

```
decide-agent/
├── src/decide_agent/            # ① Core package (publishable, src layout)
│   ├── app/                     # Assembly layer — bootstrap.py = ONLY all-knowing
│   ├── channel/                 # Adapters — CLI/HTTP/MCP/A2A (touch kernel via app.entry only)
│   ├── config/                  # Layered config (defaults < user < env vars)
│   ├── core/                    # Decision business logic — pure, fully sync
│   ├── models/                  # Adapters — provider implementations (HTTP I/O)
│   ├── plugins/                 # Adapters — plugin discovery loader (not plugins themselves)
│   ├── schemas/                 # Foundation — cross-module pydantic contracts
│   └── common/                  # Foundation — ids/jsonl/migrations
├── plugins/                     # ② Built-in tool plugins (weather/location/poi_search)
├── skills/                      # ③ Built-in scene templates (YAML only, food/general)
├── tests/                       # unit / integration / e2e / fixtures
├── scripts/                     # Wheelhouse offline build (scripts/build_wheelhouse.sh)
├── docs/                        # TECHNICAL_PLAN.md
├── deploy/                      # (reserved, empty in P0/P1)
├── examples/                    # (reserved, empty in P0/P1)
├── ARCHITECTURE.md              # Authoritative v4 architecture (561 lines, must-read)
├── docs/TECHNICAL_PLAN.md       # P1–P6 task plan
├── pyproject.toml               # Build + import-linter contracts + ruff + pytest
├── uv.lock                      # Pinned deps (committed, reproducible)
└── .env.example                 # API key placeholders (never committed keys)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add/change architecture rule | `ARCHITECTURE.md` §2 (8 laws) / §12 (directory/dependency contract) | Authoritative — code that violates = lint fail |
| Add/change stage plan | `docs/TECHNICAL_PLAN.md` §1–§7 | Acceptance criteria per stage (P1–P6) |
| Touch CLI | `src/decide_agent/channel/cli/main.py` | Typer app; sub-commands registered here |
| Touch HTTP API | `src/decide_agent/channel/http_adapter/app.py` | FastAPI; auth in `auth.py`; SSE in `events` |
| Touch MCP entry | `src/decide_agent/channel/mcp_adapter/server.py` | stdio + Streamable HTTP |
| Touch A2A | `src/decide_agent/channel/a2a/server.py` | a2a-sdk based, P6 |
| Wire a new provider | `src/decide_agent/app/bootstrap.py` → `build_provider_chain` | Also: implement `core/decision/base.py` protocol |
| Add a tool plugin | `plugins/tools/<name>/{tool.yaml,impl.py}` | Discover-loaded; no Python in core |
| Add a scene | `skills/<scene>/{skill.yaml,dimensions.yaml,info_needs.yaml,rules.yaml,prompts.yaml}` | Pure YAML — zero Python |
| Change weight resolution | `src/decide_agent/core/workflow/kernel.py` _analyze() | L1 law: weights in config, not prompts |
| Change config precedence | `src/decide_agent/config/loader.py` load_config() | defaults < user < env vars `DECIDE_AGENT__SEC__KEY=VAL` |
| Touch data layout | `src/decide_agent/config/paths.py` Paths | XDG paths, redirect via `data_dir` config |
| Build offline wheelhouse | `scripts/build_wheelhouse.sh` | For closed-network/airgapped deploy |
| Red lines (forbidden actions) | `ARCHITECTURE.md` §14 (15 items) | Must not violate |

## CODE MAP (top symbols by centrality)

| Symbol | File | Role |
|--------|------|------|
| `DecideApp` | `app/entry.py` | Channel's only legal surface |
| `build_app()` | `app/entry.py` | Production assembly (delegates to bootstrap) |
| `build_kernel()` | `app/bootstrap.py` | **Only all-knowing assembly** (L3 + §12.2 red line) |
| `DecisionKernel` | `core/workflow/kernel.py` | make_decision lifecycle orchestrator |
| `DecisionEngine` | `core/decision/engine.py` | 4 atomic question shapes over provider chain |
| `ProviderChain` | `core/decision/chain.py` | Degrade-by-availability scheduler |
| `DecisionProvider` (Protocol) | `core/decision/base.py` | Sync question-as-answer contract (4 shapes) |
| `ExperienceProvider` | `experience/provider.py` | rules.yaml-driven always-available baseline |
| `SystemOneAdapter` | `models/providers/systemone_adapter.py` | Jev cloud / Laya self-hosted (one adapter) |
| `SkillRegistry` | `skills/registry.py` | Three-level (builtin < user < project) lookup |
| `MemoryService` | `core/memory/manager.py` | three-source facade (user/session/external) |
| `PendingRegistry` | `core/workflow/pending.py` | Suspend/resume with idempotent replay |
| `StateMachine` | `core/workflow/state_machine.py` | Lifecycle state transitions |
| `StageFingerprint` | `core/workflow/fingerprint.py` | Stage-level dedup |
| `Collector` / `TemplateCollector` | `core/collect/{collector,scheduler,registry}.py` | Optional gather phase |
| `ToolPlugin` / `discover_tools` | `plugins/discovery.py` | Dynamic import of `plugins/tools/*/impl.py` |

## CONVENTIONS (project-specific, not generic)

- **Strict layering enforced** — `import-linter` contract in `pyproject.toml`: `app > {channel|plugins|models} > config > core > {schemas,common}`; **no upward deps allowed**, no side-step between siblings.
- **channels touch kernel only via `app.entry`** — never import `core/*` internals. Disobeying = lint fail.
- **`app/bootstrap.py` is the single all-knowing module** — every concrete implementation is wired exactly here. Tests use `build_kernel(...)` overrides.
- **core/ is fully synchronous** — no `async/await` inside `core/`, no `httpx`/`asyncio`. Network I/O only in `models/providers/*`.
- **cross-module language = schemas/ pydantic only** — never pass raw dicts between layers.
- **decisions are status-bivalent** — `completed` (DecisionReport) | `require_action` (PendingRequest + actions[]). No third state.
- **degrade, never fail** — missing data → `missing_information[]`, never exception to caller (law L4).
- **Provider chain auto-skips unavailable tiers** — `probe()` self-reports; chain orders by `degrade_chain` config, `experience` is always last.
- **eight architectural laws L1–L8** (see `ARCHITECTURE.md` §2) — model only judges, weights in config not prompts, options+free-text always preserved, replace-everything, never block on gap, scope-first when remembering, JSON-only outputs, four data layers.

## ANTI-PATTERNS (forbidden in this project)

- ❌ `import` from sibling layer (e.g. `core` importing `channel`) — import-linter fails CI
- ❌ `async def` inside `core/` — concurrency model locked to sync
- ❌ Hard-fail on missing data — use `missing_information[]` (L4)
- ❌ Vendor name in code (`typesafe_`, `jev_`, `laya_` prefix) — vendor names live ONLY in `models/providers/` and config (red line #3)
- ❌ External content directly into LLM prompt — must go through `ContextEngine` slots (red line #14)
- ❌ Cross-writer for `evolved/` — only core writes, owner-partitioned (red line #7)
- ❌ Silent overwrite on memory conflict — observe-then-confirm (red line #12)
- ❌ Auto-switch `decision_mode` — only recommend via metrics (red line #9)
- ❌ Sensitive info in `ask_user`/`elicitation` (red line #1)
- ❌ Bypass the layered config loader — read `os.environ` directly outside `config/loader.py`
- ❌ Add a new concrete class in `app/` other than `bootstrap.py` wiring

## UNIQUE STYLES

- **Per-decision in-process `_Session`** in `core/workflow/kernel.py` keeps request + state machine + fingerprint + candidates + score cache + ask_left + learned memory.
- **`PendingRegistry` keyed by `request_id`** — `respond()` replays same outcome on duplicate id (idempotent).
- **Three-level search chain** `builtin < user < project` repeats for both `plugins/tools/` and `skills/` (override order convention).
- **Tool plugin = manifest + impl** — no Python registration, just drop `<dir>/tool/{tool.yaml,impl.py}` and discovered on next boot.
- **Scene = pure YAML** — `skills/<scene>/{skill.yaml,dimensions.yaml,info_needs.yaml,rules.yaml,prompts.yaml}`.
- **Two-axis concurrency** — core sync / channel async; SSE push + sweeper is adapter concern.
- **`define_command`-style data dir** = XDG (`~/.local/share/decide-agent/{sessions,memory.db,raw,cache,logs,evolved,runtime}`) overridable via `data_dir` config.
- **Decision report vs Decision outcome types** — schemas split between `workflow.py` (request/outcome) and `decision.py` (result/recommendation/scores).

## COMMANDS

```bash
# install / manage
uv sync                                      # install deps

# run
uv run decide-agent chat                     # interactive REPL
uv run decide-agent demo                     # scripted e2e (tmpdir, no persistence)
uv run decide-agent init                     # write user config + .env to ~/.config/decide-agent/
uv run decide-agent tools                    # list discovered tool capabilities
uv run decide-agent serve-mcp --transport=stdio       # MCP for claude-code/opencode/codex
uv run decide-agent serve-http --port 8000            # FastAPI HTTP+SSE
uv run decide-agent serve-a2a --port 8100             # a2a-sdk server (P6)

# test
uv run pytest                                        # all 53+ tests, asyncio_mode=auto
uv run pytest tests/unit/core/workflow/              # one module
uv run pytest -k test_kernel                         # by name

# lint (line-length 100, src+tests)
uv run ruff check .
uv run ruff format .

# architecture contract (CI gate)
uv run lint-imports                                   # import-linter via pyproject

# offline distribution (closed-network/airgapped)
scripts/build_wheelhouse.sh                           # → dist/wheels/*.whl + dist/decide-agent-*.whl
uv pip install --no-index --find-links dist/wheels decide-agent   # fully offline

# package build (force-includes plugins/ + skills/ → share/decide_agent/)
uv build --wheel --out-dir dist
```

## NOTES (gotchas)

- `app.entry` is the *only* surface `channel/*` is allowed to touch. Adding a deep access (`kernel.pending.foo().bar()`) requires updating the property's docstring (e.g. `PendingRegistry` exposure).
- `kernel.snapshot()` returns pure data; persistence to `runtime/decisions/` is the **HTTP adapter's job**, not core's (red line on persistence).
- `score` cache is session-scoped, intentionally **not** restored on `restore()` — re-render = same output, no stale result.
- `experiences` writes go to `evolved/users/{owner}/...`; tests inject `tmp_path` and `owner_id="local"`.
- `TYPESAFE_API_KEY` env missing → SystemOneAdapter.probe = UNAVAILABLE → chain skips to LLMProvider → ExperienceProvider; with no env vars set, only `experience` survives (correct closed-network behavior).
- `confidence_threshold` + `confidence_penalty_per_missing` come from `defaults.yaml`, override via `DECIDE_AGENT__DECISION__CONFIDENCE_THRESHOLD` env var.
- Import `from decide_agent.app.bootstrap import ...` once at module top — `bootstrap` is the central DI; avoid re-importing primitives from `core/*` in channel adapters.
- `mc` model layer uses POST `{endpoint}/v1/systemone` — JSON RPC over HTTP; switching Jev↔Laya = change endpoint in config, no code edit (law L3).
- `DecisionOutcome.pending` only present when `status == "require_action"`; `result` only when `status == "completed"`.
