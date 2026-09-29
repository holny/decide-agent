# Contributing to decide-agent | 参与贡献

Thanks for your interest! This document covers everything you need to hack on the project productively.
感谢你的兴趣！本文档涵盖高效参与开发所需的全部内容。

---

## English

### Development setup

```bash
git clone https://github.com/<you>/decide-agent.git
cd decide-agent
uv sync                # Python ≥ 3.11, everything else is managed by uv
uv run decide-agent demo   # smoke test: scripted e2e, zero config needed
```

### Quality gates (all must pass before a PR)

```bash
uv run pytest              # 262 tests (unit + integration + e2e)
uv run ruff check .        # style
uv run lint-imports        # architecture layering contract — CI blocks violations
uv run python tests/live_matrix.py   # optional: live-LLM conversation matrix
```

### Project layout

```
src/decide_agent/
├── app/            # DecideApp entry + bootstrap (the ONLY wiring point)
├── channel/        # cli / http / mcp / a2a — transport adapters
├── config/         # layered config (defaults < user < env)
├── core/
│   ├── agent/      # P7 Agent Loop: toolkit + dialogue state
│   ├── decision/   # engine, provider chain, synthesis, pareto
│   ├── collect/    # capability DAG scheduler
│   ├── memory/     # user preference store
│   ├── workflow/   # kernel state machine, pending, fingerprint
│   └── narrator/   # output rendering (templates, zero LLM)
├── experience/     # rules-based advisor (priors, baselines, memory)
├── models/         # vendor adapters — the ONLY place with HTTP I/O & vendor names
├── plugins/        # plugin discovery loader
├── schemas/        # cross-module pydantic contracts
└── common/         # ids, logging, jsonc
plugins/tools/      # built-in tool plugins (manifest + impl)
skills/             # decision scene templates (pure jsonc, zero code)
tests/              # mirrors src/ + live_matrix.py (manual live-LLM runner)
```

### Architecture rules (enforced by `lint-imports`, CI-blocking)

Dependency direction is one-way:

```
channels → app → config → core → schemas/common
                ↘ models (vendor adapters) ↗
                     ↘ plugins ↗
```

Red lines (reviewers will reject):

1. **No upward imports** — `core/` must never import `app/`, `channel/`, or `models/`
2. **Vendor names only in `models/providers/` and config** — never in `core/`, `channel/`, or prompts
3. **No async / HTTP I/O in `core/`** — core is fully synchronous; network lives in `models/`
4. **Cross-module data = pydantic schemas** — never pass raw dicts between layers
5. **`app/bootstrap.py` is the only wiring point** — channels never import kernel internals
6. **Never crash on missing data** — degrade with disclosed gaps (L4 law)

### Common contributions

#### Add a decision scene (e.g. `laptop`)

1. `mkdir skills/laptop/` and add `dimensions.jsonc` (evaluation dims + weights), `rules.jsonc` (optional offline baseline), `prompts.jsonc` (follow-up questions), `skill.jsonc` (keywords for the offline classifier)
2. That's it — scene templates are data; discovery is automatic
3. Add a test under `tests/` mirroring an existing scene test

#### Add a data-source plugin

1. `mkdir plugins/tools/<name>/` with `tool.jsonc` (manifest) and `impl.py` (`def execute(inputs: dict)`)
2. Sync execution, no network in constructors, degrade by raising (the scheduler catches)
3. Wire into a scene via `skills/<scene>/info_needs.jsonc`

#### Add a POI vendor

1. Implement one class in `src/decide_agent/models/providers/poi_sources.py` (subclass `PoiSource`)
2. Register it in `VENDOR_SOURCES` and (optionally) the region defaults
3. Add unit tests with `httpx.MockTransport` — **no live network in tests**

### Testing guidelines

- Unit tests are **hermetic**: live vendors are force-mocked by `tests/conftest.py`; never add tests that call real APIs
- Deterministic stubs only — see `tests/unit/core/agent/test_dialogue.py` for the scripted-planner pattern
- LLM-behavior changes should be validated with `tests/live_matrix.py` locally, but don't commit live-LLM assertions into pytest

### Pull requests

1. Fork → feature branch → keep diffs focused (one concern per PR)
2. All gates green (see above)
3. Describe **behavior changes** explicitly — reviewers pay special attention to pipeline semantics
4. **AI-assisted contributions are welcome but must be disclosed** in the PR description (what was AI-generated, what you verified yourself). Undisclosed bulk AI content may be rejected — this protects both you and the maintainers.

---

## 中文

### 开发环境

```bash
git clone https://github.com/<you>/decide-agent.git
cd decide-agent
uv sync
uv run decide-agent demo   # 冒烟：脚本化 e2e，零配置
```

### 质量门禁（PR 前全部通过）

```bash
uv run pytest              # 262 用例
uv run ruff check .        # 风格
uv run lint-imports        # 架构分层契约（CI 阻断违规）
uv run python tests/live_matrix.py   # 可选：真实 LLM 全场景矩阵
```

### 架构红线（reviewer 直接拒绝）

1. **禁止向上依赖**：`core/` 不得 import `app/`、`channel/`、`models/`
2. **厂商名只出现在 `models/providers/` 与 config**
3. **`core/` 禁 async、禁网络 I/O**（全同步，网络在 `models/`）
4. **跨模块数据 = pydantic schema**，不传裸 dict
5. **`app/bootstrap.py` 是唯一装配点**，通道不碰内核内部
6. **缺数据永不崩溃**——降级并披露（L4 法则）

### 常见贡献方式

| 想加什么 | 怎么做 | 代码量 |
|----------|--------|--------|
| 决策场景 | `skills/<场景>/` 放 jsonc 模板 | 零代码 |
| 数据源插件 | `plugins/tools/<名>/` manifest + impl | 一个小文件 |
| POI 厂商 | `poi_sources.py` 一个子类 + MockTransport 单测 | ~40 行 |
| 新 LLM | config 指向任意 OpenAI 兼容端点 | 零代码 |

### 测试约定

- 单测**全封闭**：真实厂商源被 conftest 强制 mock，禁止真实网络断言
- LLM 行为改动用 `tests/live_matrix.py` 本地验证，勿把 live 断言提交进 pytest

### PR 规范

1. Fork → 分支 → 每个 PR 只解决一件事
2. 全部门禁通过
3. **行为变更显式说明**（reviewer 重点关注管道语义）
4. **AI 辅助贡献欢迎，但必须在 PR 描述中披露**（哪些部分 AI 生成、你自行验证了什么）——未披露的批量 AI 内容可能被拒绝

### 行为准则

保持友善、就事论事。有问题先开 issue 讨论，避免大型 PR 突袭。

---

<div align="center">

License: [Apache-2.0](LICENSE)

</div>
