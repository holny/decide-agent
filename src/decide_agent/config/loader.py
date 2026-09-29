"""Layered config loader: 环境变量(.env) > 用户 config.yaml > 内置 defaults.jsonc。

提供 `decide-agent init` 一键生成用户配置模板。
"""
import os
from functools import lru_cache
from pathlib import Path

import yaml

from decide_agent.config.paths import user_config_file

_BUILTIN_DEFAULTS = Path(__file__).resolve().parent / "defaults.jsonc"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache
def load_config(user_config_path: str | None = None) -> dict:
    """Merge: defaults < user config < env vars (DECIDE_AGENT__SECTION__KEY=VAL dotted form)."""
    from decide_agent.common.jsonc import load as load_jsonc

    config = load_jsonc(_BUILTIN_DEFAULTS) or {}
    cfg_path = Path(user_config_path) if user_config_path else user_config_file()
    if cfg_path.exists():
        if cfg_path.suffix in (".jsonc", ".json"):
            from decide_agent.common.jsonc import load as load_jsonc

            user_conf = load_jsonc(cfg_path) or {}
        else:  # 旧版 config.yaml 兼容读取
            user_conf = yaml.safe_load(cfg_path.read_text("utf-8")) or {}
        config = _deep_merge(config, user_conf)
    env_overrides = _collect_env_overrides()
    if env_overrides:
        config = _deep_merge(config, env_overrides)
    return config


def _collect_env_overrides() -> dict:
    """DECIDE_AGENT__decision__confidence_threshold=0.5 → {"decision": {...}}."""
    prefix = "DECIDE_AGENT__"
    tree: dict = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        leaf = parts[-1]
        node[leaf] = yaml.safe_load(value)
    return tree


def get_confidence_params() -> tuple[float, float]:
    conf = load_config().get("decision", {})
    return float(conf.get("confidence_threshold", 0.65)), float(
        conf.get("confidence_penalty_per_missing", 0.40))


def get_decision_mode() -> str:
    """auto（provider 链逐级降级）| experience（纯规则零模型）。"""
    return str(load_config().get("decision", {}).get("decision_mode", "auto"))


def get_degrade_chain() -> list[str]:
    return list(load_config().get("decision", {}).get("degrade_chain",
                                                     ["decision_model", "llm", "experience"]))


def get_stage_budgets() -> dict[str, float | None]:
    raw = load_config().get("decision", {}).get("provider_timeouts", {})
    return {stage: (None if raw.get(stage) is None else float(raw[stage]))
            for stage in ("collect", "decide", "analysis")}


def get_output_format() -> str:
    fmt = str(load_config().get("output", {}).get("format", "json_minimal"))
    return fmt if fmt in ("json_minimal", "json_full", "text") else "json_minimal"


def get_output_language() -> str:
    """auto = 按用户输入自动检测（kernel 写入 outcome.language）；zh/en = 强制。"""
    lang = str(load_config().get("output", {}).get("language", "auto")).lower()
    return lang if lang in ("zh", "en", "auto") else "auto"


def get_decision_model() -> dict:
    return dict(load_config().get("decision_model", {}))


def get_llm_provider_config() -> dict:
    return dict(load_config().get("llm", {}))


def get_tools_config() -> dict:
    """工具数据源配置：region / poi_search{radius,scene_query,providers} / vendor 连接参数。"""
    return dict(load_config().get("tools", {}))


def get_http_auth() -> dict:
    return dict(load_config().get("http", {}).get("auth", {"enabled": False}))


def get_data_dir() -> str:
    return str(load_config().get("paths", {}).get("data_dir", ""))


INIT_TEMPLATE = """// Decide-Agent 用户配置（config.jsonc；支持注释；删除不需要的行即可用内置默认）
// 未配置的 provider 层自动不可用（链降级）；逐项说明见 docs/CONFIG.md
{
  "decision": {
    "confidence_threshold": 0.65,
    "confidence_penalty_per_missing": 0.4,
    "decision_mode": "auto",
    "provider_timeouts": { "collect": 5.0, "decide": 8.0, "analysis": 3.0 }
  },
  "output": { "format": "json_minimal", "language": "auto" },
  // 判断模型层：endpoint+model 都配置才启用；密钥经 api_key_env 引用环境变量
  "decision_model": {
    "endpoint": "https://your-decision-model.example.com",
    "api_key_env": "DECISION_MODEL_API_KEY",
    "model": "your-model-name"
  },
  // 判断②级兜底 LLM（OpenAI 兼容）
  "llm": {
    "base_url": "https://your-llm-endpoint.example.com/v1",
    "api_key_env": "LLM_API_KEY",
    "model": "your-llm-model"
  },
  "http": { "auth": { "enabled": false } },
  "paths": { "data_dir": "~/.local/share/decide-agent" }
}
"""


def write_user_config(path: Path | None = None) -> Path:
    target = path or user_config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(INIT_TEMPLATE, "utf-8")
    return target
