# skills/ — Built-in Scene Templates (Pure YAML)

**Role:** Scene-specific decision templates. **Pure YAML — zero Python.** Loaded by `src/decide_agent/skills/registry.py`.

## STRUCTURE

```
skills/
└── food/
    ├── SKILL.md               # description + 关键词 section (parsed for keyword classify)
    ├── skill.yaml             # metadata (name, display, version, keywords, requires)
    ├── dimensions.yaml        # {display, weight, source, rubric[]} per dimension
    ├── info_needs.yaml        # collect[{capability, degrade}] + dependencies + follow_up
    ├── rules.yaml             # baseline rules (when/then/elif)
    └── prompts.yaml           # question text + options per dimension + output format
```

## WHERE TO LOOK

| File | Format | Used by |
|------|--------|---------|
| `SKILL.md` | markdown with `## 关键词` section | `_extract_keywords` → experience provider fallback |
| `skill.yaml` | YAML (`name, display, version, keywords[], requires.capabilities[]`) | `SkillRegistry._load` → scene metadata |
| `dimensions.yaml` | YAML (`dimensions.<dim>: {display, weight, source, rubric[]}`) | synthesis weights + collect source mapping |
| `info_needs.yaml` | YAML (`collect[{capability, degrade}], dependencies{}, follow_up`) | `CollectScheduler` DAG topology |
| `rules.yaml` | YAML (`when/then/elif` rule expressions) | `experience/_answer_score` evaluation |
| `prompts.yaml` | YAML (`questions.<dim>: {text, options[], allow_free_text}`) | `kernel._question_resolver` |

## CONVENTIONS

- **Pure YAML** — no Python files in scene dirs. Drop a scene = install (skill registry discovers).
- **Override order** — `builtin < user < project`. Same-named scene in higher tier wins.
- **`requires.capabilities[]`** — listed capabilities must be discoverable; missing = scene considered unsupportable.
- **`source` in dimensions.yaml** — name of capability to call for that dimension (e.g. `source: weather` → collect uses weather plugin).
- **`keywords` in skill.yaml** — fallback for `DecisionEngine.classify` when `experience` provider runs (rule-based, not LLM).
- **`rubric[]`** in dimensions.yaml — direct feed to `score` primitive (per ARCHITECTURE §8 schema).
- **`follow_up` in info_needs.yaml** — semantic follow-up after main collect; A/B拍 uses these.

## ANTI-PATTERNS

- ❌ Adding Python files in `skills/<scene>/` — YAML only
- ❌ Hardcoded weight values in code — weight = config (L1 law)
- ❌ Editing `dimensions.yaml` for runtime overrides — use `weights_override.<scene>` in user config
- ❌ Skipping `info_needs.dependencies{}` — DAG topology requires explicit declaration
- ❌ Mixing scenario-specific questions into general prompts.yaml — each scene owns its own

## NOTES

- **Built-in `food/` scene** (`skills/food/skill.yaml`):
  - `name: food`
  - `display: 美食`
  - `version: 0.1.0`
  - `keywords: [吃饭, 吃, 餐, 饿, 美食, 饭菜, 点菜, 下馆子]`
  - `requires.capabilities: [location, poi_search]`
- **`SKILL.md` 关键词 section**: parsed by `_extract_keywords` (line-by-line until next `##`). Lines starting with `#` are ignored.
- **Weight override via config**: `weights_override.food.taste_match: 0.40` in user config — applied during `SkillRegistry._load` (only changes weight, not other dimension fields).
- **P1 minimal schema**: just the five YAML files. P5 may add `examples/` (intent training data for classify).
- **Wheel packaging**: `pyproject.toml` force-includes `skills/` → `share/decide_agent/skills/` in installed wheel.
- **General scene**: no `skills/general/` directory — general mode is implicit (when classify fails or no scene matches, kernel uses `general` and user provides candidates).