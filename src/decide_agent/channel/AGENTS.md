# src/decide_agent/channel — Transport Adapters (Four Surface Forms)

**Role:** The "four马甲" — same kernel, four transports. Touch core **only via `app.entry`**.

## STRUCTURE

```
channel/
├── __init__.py
├── cli/main.py                # Typer app: chat / demo / init / tools / serve-{mcp,http,a2a}
├── http_adapter/
│   ├── app.py                 # FastAPI: /v1/decisions | /events (SSE) | /respond | /feedback | /memory | /tools | /capabilities
│   └── auth.py                # API Key / Bearer (config http.auth, default OFF, red line #15)
├── mcp_adapter/server.py      # MCP stdio + Streamable HTTP
├── a2a/server.py              # a2a-sdk based server (P6)
└── shared/                    # shared transport helpers
    ├── events.py              # EventStore: append-only event log per decision
    ├── snapshots.py           # DecisionSnapshotStore: persist to runtime/decisions/
    └── sweeper.py             # PendingSweeper: periodic expire_due() + emit EXPIRED
```

## WHERE TO LOOK

| Transport | File | Notes |
|-----------|------|-------|
| CLI dispatch | `cli/main.py::app` (typer.Typer) | Sub-commands registered here; demos use `tmp_path` |
| HTTP entry | `http_adapter/app.py::create_http_app` | Tests inject `decide` (DecideApp instance) |
| HTTP auth | `http_adapter/auth.py::api_key_dependency` | Reads `app.state.http_auth` |
| SSE streaming | `http_adapter/app.py::events` endpoint | `StreamingResponse(media_type=text/event-stream)` |
| Snapshot persistence | `shared/snapshots.py::DecisionSnapshotStore` | Saves to `runtime/decisions/{id}.json` |
| Pending sweep | `shared/sweeper.py::PendingSweeper.start()` | Background thread, period configurable |
| EventStore | `shared/events.py::EventStore` | Append-only, replay by `(decision_id, after_seq)` |
| MCP stdio | `mcp_adapter/server.py::run(transport="stdio")` | For Claude/opencode/codex |
| MCP HTTP | `mcp_adapter/server.py::run(transport="streamable-http")` | Streamable HTTP transport |
| A2A server | `a2a/server.py::build_a2a_app` | P6 — AgentCard + message/send |

## CONVENTIONS

- **Channel → kernel only via `app.entry`** — `from decide_agent.app.entry import build_app`, then `build_app()`. Never import `core/*` directly.
- **HTTP persistence is HTTP's job** — `_persist(app, outcome)` writes to `runtime/decisions/` after every `make_decision` / `respond`. Restart recovery via `snapshots.load_all()`.
- **Auth is config-driven, default OFF** — `http.auth.enabled: false` by default. Production deployments MUST enable (red line #15).
- **Owner-id is required for memory ops** — `memory_for(owner_id)` creates per-request MemoryService; never share across owners.
- **SSE events are `(type, decision_id, seq, payload)`** envelopes. Client resumes with `?after_seq=N`.
- **Sweeper is per-process** — `PendingSweeper.start()` returns handle; FastAPI shutdown event stops it.

## ANTI-PATTERNS

- ❌ Importing `core/*` from `channel/` — use `app.entry` only
- ❌ Bypassing `api_key_dependency` — every `/v1/*` endpoint must have it
- ❌ Storing cross-owner data in shared memory — `memory_for(owner_id)` per-request
- ❌ Reading env vars directly — `config/loader.get_http_auth()` etc.
- ❌ Hardcoded auth keys — always via env var per config (`http.auth.api_key_env`)
- ❌ Auto-disabling SSE for non-streaming clients — they should use REST POST + GET events

## NOTES

- **HTTP endpoints (P3)**:
  - `POST /v1/decisions` — start decision → returns outcome
  - `GET /v1/decisions/{id}/events` — SSE event stream (resumable via `?after_seq=N`)
  - `POST /v1/decisions/{id}/respond` — answer pending request (idempotent by request_id)
  - `POST /v1/decisions/{id}/feedback` — accept/reject/adjust
  - `GET/DELETE /v1/memory/{owner_id}` — privacy compliance export/delete
  - `GET /v1/tools` — discovered capabilities
  - `GET /v1/capabilities` — by-side grouping
- **SSE reconnection**: client sends `?after_seq=N` to resume from last seen event seq.
- **`PendingExpiredError` → HTTP 409** with DecisionError body (red line #13).
- **`/healthz` is unauthenticated** for liveness checks.
- **MCP elicitation**: only flat schemas; `decline/cancel` → degrade to next tier; never request sensitive info (red line #1).
- CLI `demo` uses `tempfile.TemporaryDirectory()` → no user data pollution.