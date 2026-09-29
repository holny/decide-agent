"""Config layering + skill-search-chain tests."""

from decide_agent.config.loader import _collect_env_overrides, _deep_merge
from decide_agent.config.paths import Paths


def test_deep_merge_nested():
    merged = _deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 2}})
    assert merged == {"a": {"x": 1, "y": 2}, "b": 1}


def test_env_overrides(monkeypatch):
    monkeypatch.delenv("DECIDE_AGENT__TOOLS__POI_SEARCH__PROVIDERS", raising=False)  # conftest 夹具隔离
    monkeypatch.setenv("DECIDE_AGENT__decision__confidence_threshold", "0.5")
    overrides = _collect_env_overrides()
    assert overrides == {"decision": {"confidence_threshold": 0.5}}


def test_paths_resolve(tmp_path):
    p = Paths.resolve(tmp_path / "data")
    assert p.root == tmp_path / "data"
    assert p.raw == tmp_path / "data" / "raw"
