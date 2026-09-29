"""P1-8 config tests: new keys, typed getters, builtin dir resolution."""
from decide_agent.config import paths as config_paths
from decide_agent.config.loader import (
    get_decision_mode,
    get_decision_model,
    get_degrade_chain,
    get_http_auth,
    get_output_format,
    get_stage_budgets,
)


def test_defaults_new_keys():
    assert get_decision_mode() == "auto"
    assert get_degrade_chain() == ["decision_model", "llm", "experience"]
    budgets = get_stage_budgets()
    assert budgets["collect"] == 5.0 and budgets["analysis"] == 3.0
    assert get_output_format() == "json_minimal"
    model = get_decision_model()
    assert model["endpoint"].startswith("https://")
    assert model["api_key_env"] == "TYPESAFE_API_KEY"  # 密钥走 env，永不落盘
    assert get_http_auth()["enabled"] is False


def test_output_format_env_override(monkeypatch):
    monkeypatch.setenv("DECIDE_AGENT__output__format", "text")
    from decide_agent.config import loader

    loader.load_config.cache_clear()
    assert get_output_format() == "text"
    loader.load_config.cache_clear()
    monkeypatch.setenv("DECIDE_AGENT__output__format", "bogus")
    loader.load_config.cache_clear()
    assert get_output_format() == "json_minimal"  # 非法值回默认
    loader.load_config.cache_clear()


def test_builtin_share_dirs_dev_mode():
    plugins_dir, skills_dir = config_paths.builtin_share_dirs()
    assert (plugins_dir / "tools" / "location" / "tool.jsonc").exists()
    assert (skills_dir / "food" / "rules.jsonc").exists()


def test_builtin_share_dirs_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DECIDE_AGENT_BUILTIN_DIR", str(tmp_path))
    plugins_dir, skills_dir = config_paths.builtin_share_dirs()
    assert plugins_dir == tmp_path / "plugins"
    assert skills_dir == tmp_path / "skills"


def test_weights_override_updates_not_replaces(monkeypatch):
    """覆盖语义：weights_override 只改列出键的数值，不替换维度集（真实缺陷回归）。"""
    from decide_agent.app import bootstrap
    from decide_agent.config import loader

    base_config = {
        "weights_override": {"food": {"taste_match": 0.4}},
    }
    monkeypatch.setattr(loader, "load_config", lambda path=None: base_config)
    weights = bootstrap.resolve_scene_weights("food")
    assert set(weights) == {"taste_match", "distance", "price", "queue", "weather_fit"}
    assert weights["taste_match"] == 0.4  # 覆盖键生效
    assert weights["distance"] == 0.2     # 未列键保留模板值
