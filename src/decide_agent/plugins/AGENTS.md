# src/decide_agent/plugins — Plugin Discovery Loader (Adapter)

**Role:** Dynamic import of `plugins/tools/*/impl.py` based on manifest YAML. **Loader lives in core; plugins live in `plugins/` repo root.**

## STRUCTURE

```
plugins/
├── __init__.py
└── discovery.py               # ★ discover_tools + PluginExecutor
```

## WHERE TO LOOK

| Need | File | Notes |
|----|------|-------|
| Discover tool plugins | `discovery.py::discover_tools(search_dirs)` | Scans `<dir>/<tool>/tool.jsonc + impl.py` |
| Define a new tool | `plugins/tools/<name>/tool.jsonc + impl.py` (at repo root, not here) | Drop dir → discovered on next boot |
| Load impl.py dynamically | `discovery.py::ToolPlugin._load_impl()` | `importlib.util.spec_from_file_location` |
| Call a tool | `discovery.py::ToolPlugin.execute(inputs)` | Loads on first call |
| Aggregate executor | `discovery.py::PluginExecutor.execute(capability, inputs)` | Adapter glue for `CollectScheduler` |

## CONVENTIONS

- **Manifest = `tool.jsonc`** — minimal fields: `name`, `side`, `description`, `inputs`, `outputs`. Validation is permissive (load with try/except, skip invalid + warn).
- **Impl = `impl.py`** — must define `def execute(inputs: dict) -> dict`. Loaded via `importlib.util.spec_from_file_location`.
- **Tool name = capability name** — `discover_tools` returns `{name: ToolPlugin}`; `CapabilityRegistry` registers by `name`.
- **Search dir override order** — later dirs override same-name tools. Matches `builtin < user < project` semantics.
- **Invalid plugin = skip + warn** — `print(f"[plugins] skip invalid tool plugin {tool_dir}: {exc}")`. Never raises; never blocks boot.
- **Module name fake-prefixed** — `decide_agent_plugin_<name>` to avoid namespace collisions.

## ANTI-PATTERNS

- ❌ Putting tool implementations in `src/decide_agent/plugins/` — they live at repo root `plugins/tools/*/`
- ❌ Adding Python `setup.py`-style registration — manifest-only discovery
- ❌ Importing `core.decision.*` here — `discovery.py` is pure loader, no business logic
- ❌ Hardcoded plugin list — directory scan is the source of truth
- ❌ Raising on invalid plugin — skip + warn (L4)

## NOTES

- **Built-in plugins** (repo root `plugins/tools/`):
  - `weather/` — mock: returns `{condition: "小雨", temperature_c: 15.0, precipitation: True}`
  - `location/` — location capability
  - `poi_search/` — POI search
- **Builtin dir resolution** via `config/paths.py::builtin_share_dirs()`:
  - Env var `DECIDE_AGENT_BUILTIN_DIR` set → use that root
  - Repo mode → `plugins/` + `skills/` at repo root
  - Installed wheel → site-packages `share/decide_agent/{plugins,skills}` (force-included by hatch)
- **P1 minimal manifest**: only `tool.jsonc` + `impl.py`. Full uniform manifest (with `kind`, `requires`, etc.) lands P5+.
- **Dynamic import = new deps require no rebuild** — drop a tool with a `requirements.txt` and re-run. Trade-off documented in ARCHITECTURE §12.4 ("不做二进制编译").
- **First call loads module** — subsequent calls reuse. No reload semantics.