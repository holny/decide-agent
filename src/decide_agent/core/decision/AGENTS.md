# src/decide_agent/core/decision — Decision Engine + Provider Chain

**Role:** 4 atomic question shapes (`classify` / `score` / `extract` / `verify`) + zero-model decision synthesis.

## STRUCTURE

```
decision/
├── __init__.py                # exports DecisionEngine
├── base.py                    # ★ DecisionProvider Protocol + JudgmentSink Protocol
├── chain.py                   # ProviderChain: probe + run + degrade-by-availability
├── engine.py                  # DecisionEngine: typed question → chain.answer(...)
├── availability.py            # ProbeResult + key_probe (env-driven)
├── synthesis.py               # Pure code: weighted merge + cross_validate (no model)
├── gating.py                  # Confidence gate (threshold + missing penalty)
└── sandbox.py                 # potential_flip_dims (heuristic only, ask-decision)
```

## WHERE TO LOOK

| Need | File / Symbol | Notes |
|----|---------------|-------|
| Implement a new provider | `base.py::DecisionProvider` (Protocol, runtime_checkable) | Implementers: `experience/provider.py` + `models/providers/*` |
| Change provider order | `core/decision/chain.py` — chain reads `degrade_chain` from config (default `decision_model → llm → experience`) | `experience` is always last fallback |
| Change score synthesis formula | `synthesis.py::synthesize(dimension_scores)` | Pure code, zero model (L1) |
| Change confidence gate | `gating.py::evaluate(missing_dims, n_candidates, threshold, penalty)` | Returns `(should_ask, confidence, missing_dimensions)` |
| Adjust ask-decision heuristic | `sandbox.py::potential_flip_dims` | Returns dims whose best/worst values flip rank |
| Probe a provider's availability | `availability.py::ProbeResult` — provider `probe()` self-reports | Chain skips UNAVAILABLE tiers |
| Cross-validate score vs choice | `synthesis.py::cross_validate` | agree=+boost, disagree=-cut, choice rules tiebreak |

## CONVENTIONS

- **`DecisionProvider` Protocol defined by consumer (decision domain)** — providers `answer(question) → TypedAnswer`, raise on failure (chain catches + degrades).
- **`provider.probe()` self-reports availability** — no central probe registry. LLM provider without 配置的 api_key_env returns `UNAVAILABLE` and chain skips.
- **One structural implementer in core**: `ExperienceProvider` (always-available baseline). Network I/O providers live in `models/providers/*` (whitelisted by lint).
- **Question shapes are uniform** — `TypedQuestion{shape, scene, dimension, text, slot, context}`. Context dict carries `candidate/env/slots`.
- **`answer()` may raise** — chain catches and tries next tier; degraded answers carry `degraded=true`.

## ANTI-PATTERNS

- ❌ Vendor-specific code (`typesafe_`, `jev_`) in `core/` — only in `models/providers/` and config (red line #3)
- ❌ Adding `httpx`/`async`/`asyncio` imports — sync only (red line §12.2)
- ❌ Synthesizing in `synthesis.py` via LLM — pure code, weight = config (L1 law)
- ❌ Silently mutating chain order at runtime — read once at boot
- ❌ Putting `ExperienceProvider` in `models/` — it implements the core protocol and lives in `experience/`

## NOTES

- `experience` provider raises `UnsupportedShape` for `extract`/`verify` (handles score/classify only) — chain auto-skips to `llm` for those shapes.
- `cross_validate` defaults: `boost=0.05`, `cut=0.15` (pure Python literals, not config).
- Sandbox uses **quick heuristic scoring only**, not full chain re-runs — invoked at ask-decision time only (per red line #10).
- `JudgmentSink` Protocol (also in `base.py`) is implemented by `experience/recorder.py::JudgmentRecorder` (per-bucket JSONL).