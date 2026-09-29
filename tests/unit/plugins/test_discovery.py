"""Tool plugin discovery tests: repo-root plugins load & execute; invalid skipped."""
import json
from pathlib import Path

from decide_agent.plugins.discovery import PluginExecutor, discover_tools

REPO_PLUGINS = Path(__file__).resolve().parents[3] / "plugins" / "tools"


def test_discover_repo_plugins():
    plugins = discover_tools([REPO_PLUGINS])
    assert {"location", "weather", "poi_search"} <= set(plugins)
    assert plugins["location"].side == "server"


def test_execute_mock_tools():
    executor = PluginExecutor(discover_tools([REPO_PLUGINS]))
    location = executor.execute("location", {})
    assert isinstance(location["city"], str)  # 真实定位，城市名因环境而异
    weather = executor.execute("weather", {})
    assert isinstance(weather["condition"], str) and weather["condition"]  # 真实天气，断言结构
    candidates = executor.execute("poi_search", {})
    assert len(candidates) == 8
    assert candidates[0]["name"] == "蜀香居"


def test_unknown_capability_raises():
    executor = PluginExecutor({})
    import pytest

    with pytest.raises(KeyError):
        executor.execute("nope", {})


def test_invalid_plugin_skipped(tmp_path, caplog):
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "tool.jsonc").write_text("name: [broken", encoding="utf-8")
    (bad / "impl.py").write_text("def execute(i): return 1\n", encoding="utf-8")
    plugins = discover_tools([tmp_path])
    assert plugins == {}
    assert any("skip invalid tool plugin" in r.message for r in caplog.records)


def test_user_tool_overrides_builtin(tmp_path):
    """用户自定义 location 工具 → 覆盖内置同名 → 用户实现生效（L3 可替换）。"""
    user_dir = tmp_path / "user_tools"
    user_loc = user_dir / "location"
    user_loc.mkdir(parents=True)
    (user_loc / "tool.jsonc").write_text(json.dumps({
        "kind": "tool_server", "name": "location", "side": "server",
        "description": "用户自定义高德定位",
    }), encoding="utf-8")
    (user_loc / "impl.py").write_text(
        'def execute(inputs):\n'
        '    return {"lat": 39.9, "lng": 116.4, "city": "北京", "accuracy": "lbs"}\n',
        encoding="utf-8",
    )
    plugins = discover_tools([REPO_PLUGINS, user_dir])
    assert plugins["location"].description == "用户自定义高德定位"
    result = plugins["location"].execute({})
    assert result["city"] == "北京"  # 用户版本生效，非内置上海徐汇
    # 未被覆盖的内置工具照常（真实天气 → 断言结构非字面值）
    weather = plugins["weather"].execute({})
    assert isinstance(weather["condition"], str) and weather["condition"]
