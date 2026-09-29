# plugins/ — Built-in Tool Plugins

**Role:** External info source plugins. Discovered via `core/plugins/discovery.py::discover_tools`. **Drop a directory = install** (no Python registration).

## STRUCTURE

```
plugins/
└── tools/
    ├── weather/
    │   ├── tool.yaml          # manifest
    │   └── impl.py            # def execute(inputs: dict) -> dict
    ├── location/
    │   ├── tool.yaml
    │   └── impl.py
    └── poi_search/
        ├── tool.yaml
        └── impl.py
```

## WHERE TO LOOK

| Plugin | Manifest fields | Mock impl |
|--------|----------------|-----------|
| `weather` | `name: weather, side: server, description: 查询天气（mock）, outputs: {condition, temperature_c}` | returns `{condition: "小雨", temperature_c: 15.0, precipitation: True}` |
| `location` | location capability | location-aware mock |
| `poi_search` | POI search | placeholder |

## CONVENTIONS

- **Each plugin = one directory** — `<plugin-name>/{tool.yaml, impl.py}`. Side-by-side mandatory; missing either = skipped.
- **`tool.yaml` minimal fields** (P1): `name`, `side` ∈ `{server, mcp, client, memory}`, `description`, `inputs` (dict), `outputs` (dict). P5 adds `kind`, `requires`, etc.
- **`impl.py::execute(inputs: dict) -> dict`** — sync function. Loaded on first call via `importlib.util.spec_from_file_location`.
- **Add a new plugin**: drop a directory with the two files; restart server. No registration code.
- **Side determines priority** — `client > server > mcp > memory` (collect scheduler uses registration order).
- **Builtin dir resolution** via `config/paths.py::builtin_share_dirs()` — repo root in dev, `share/decide_agent/plugins/` in installed wheel.
- **Invalid plugins skipped with warning** at boot. Plugin continues operating.

## ANTI-PATTERNS

- ❌ Adding Python `setup.py`-style registration — directory-scan only
- ❌ Async `def execute` — must be sync (tool runs in collect scheduler thread pool, but kernel is sync)
- ❌ Hardcoded config in `impl.py` — pass via `inputs` from caller
- ❌ Side-stepping the manifest — `tool.yaml` is the contract
- ❌ Putting plugin code under `src/` — must be at repo root or installed `share/decide_agent/plugins/`

## NOTES

- **Three mock plugins** ship in `plugins/tools/` — all return static data for P0/P1 demo. Real data sources (Amap, Baidu) wire in P4.
- **Bootstrap wires plugins**: `bootstrap.py::build_collector` calls `discover_tools([plugins_dir / "tools"])`, then registers each into `CapabilityRegistry` as `Capability(name, side, description)`.
- **Capability listing endpoint**: `GET /v1/tools` returns the discovered list. `decide-agent tools` CLI sub-command does the same.
- **Wheel packaging**: `pyproject.toml` force-includes `plugins/` → `share/decide_agent/plugins/`. Hatchling auto-discovers and bundles.
- **Test isolation**: tests use `discover_tools([tmp_path / "tools"])` with hand-rolled manifest + impl files.
- **Future kinds** (P5+): `scene_template`, `mcp_server`, `presenter` — same manifest format, different discovery paths.