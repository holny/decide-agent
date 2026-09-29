# src/decide_agent/models/providers — Model Adapters (HTTP I/O)

**Role:** Implement `core.decision.base.DecisionProvider`. **Only place where vendor names appear** (red line #3). Switch endpoint to switch vendor (law L3 — replace everything).

## STRUCTURE

```
models/providers/
├── __init__.py
├── systemone_adapter.py       # ★ SystemOneAdapter: Jev cloud / Laya self-hosted via one POST /v1/systemone
└── llm_provider.py            # LLMProvider: OpenAI/Anthropic-compatible chat API, JSON Schema forced
```

## WHERE TO LOOK

| Need | File | Notes |
|----|------|-------|
| Switch decision_model vendor | `systemone_adapter.py::SystemOneAdapter(endpoint=...)` | Jev cloud default; Laya = change `endpoint` only |
| Wire a new provider | implement `DecisionProvider` Protocol here, register in `app/bootstrap.py::build_provider_chain` | One file per provider |
| Configure decision_model | `config/defaults.jsonc::decision_model.endpoint` / `api_key_env` / `model` | `api_key_env` = name of env var holding key (never the key itself) |
| Configure LLM fallback | `config/defaults.jsonc::llm` section | `api_key_env` / `base_url` / `model` / `timeout_ms` |
| Probe availability | `models/providers/systemone_adapter.py::probe` returns `ProbeResult` | `key_probe(api_key_env)` reads env, returns UNAVAILABLE if missing |
| Force JSON output | `llm_provider.py` uses response_format / json_schema | Provider must return confidence + basis (per TypedAnswer) |

## CONVENTIONS

- **Implementers of `core.decision.base.DecisionProvider`** — structural typing (Protocol). Lint whitelists `models/providers/*` → `core.decision.base`.
- **Vendor names ONLY here and in config** — never `typesafe_*` / `jev_*` / `laya_*` in `core/`, `schemas/`, `channel/`, etc. (red line #3).
- **One endpoint, two vendors** — SystemOneAdapter serves Jev cloud (default) AND Laya self-hosted. Both implement POST `/v1/systemone`. Endpoint config decides which.
- **API keys via env var only** — `api_key_env`（外部配置的 env 名）. Code reads `os.environ[api_key_env]`; never hardcode or persist key.
- **`probe()` is self-reporting** — no central registry. Missing key → UNAVAILABLE → chain auto-skips.
- **Transport failures → raise** — chain catches and tries next tier (L4 degrade).
- **`timeout_ms` default = 5000 (decision_model) / 8000 (llm)** — both configurable in `defaults.jsonc`.

## ANTI-PATTERNS

- ❌ Vendor prefix in code or variable names (`jev_endpoint`, `laya_payload`) — use generic `endpoint`/`model`/`payload`
- ❌ Hardcoding API keys — read env var via `api_key_env` config
- ❌ Returning response without `confidence` field — `TypedAnswer` requires confidence; `systemone` raises if missing
- ❌ Network I/O in `core/` — adapter lives here, not in `core/decision/`
- ❌ Adding a third vendor as a sibling module — extend SystemOneAdapter or add a new adapter here

## NOTES

- **SystemOneAdapter transport**: `httpx.Client(timeout=timeout_s)` synchronous; `POST {endpoint}/v1/systemone` JSON body.
- **Question shape → primitive mapping** (in SystemOneAdapter):
  - `classify` → `choice`
  - `score` → `score`
  - `extract` → `choice`
  - `verify` → `noul`
- **Response shape**: `{value?, distribution?, confidence, basis?}`. Missing `confidence` → raise `RuntimeError`.
- **LLMProvider**: OpenAI/Anthropic-compatible chat API. Forces JSON Schema output. Self-reports confidence (calibrated). Used as 2nd-tier fallback after `decision_model`.
- **Closed-network / 内网 mode**: set `decision_model.endpoint` to local Laya, or omit keys → chain auto-skips to `experience` (zero-model).
- **Laya self-hosted** supports hooks, escalate headers, batched requests, routing — capability negotiation deferred to P4.
- **Smoke test for closed networks**: `decision_mode: experience` → zero model calls, full chain runs on rules alone.