"""Minimal tool-plugin discovery (P1): <dir>/<tool>/tool.jsonc + impl.py.

统一 manifest（scene_template/tool_server/mcp_server/presenter 四类 kind）与
完整生命周期校验在 P5/P6-1 落地；本模块只服务工具插件的外置加载。
非法插件跳过并告警（插件策略，永不阻断主流程）。
"""
import importlib.util
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from decide_agent.common.jsonc import load as load_jsonc
from decide_agent.plugins.manifest import validate_capability_name, validate_plugin_dir
from decide_agent.schemas.collect import Side


class ToolPlugin(BaseModel):
    name: str
    side: Side
    description: str = ""
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    source_dir: Path

    def execute(self, inputs: dict) -> Any:
        module = self._load_impl()
        return module.execute(inputs)

    def _load_impl(self):
        impl_path = self.source_dir / "impl.py"
        spec = importlib.util.spec_from_file_location(
            f"decide_agent_plugin_{self.name}", impl_path,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load impl for tool '{self.name}'")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


log = logging.getLogger(__name__)


def discover_tools(search_dirs: list[Path]) -> dict[str, ToolPlugin]:
    """Scan <dir>/<tool>/tool.jsonc + impl.py；manifest 校验（P5）+ 后目录覆盖同名。"""
    found: dict[str, ToolPlugin] = {}
    for directory in search_dirs:
        if not Path(directory).exists():
            continue
        for tool_dir in sorted(Path(directory).iterdir()):
            manifest = tool_dir / "tool.jsonc"
            impl = tool_dir / "impl.py"
            if not manifest.exists() or not impl.exists():
                continue
            try:
                validate_plugin_dir(tool_dir)  # P5 统一校验（kind/必需文件）
                data = load_jsonc(manifest) or {}
                plugin = ToolPlugin(source_dir=tool_dir, **data)
                for w in validate_capability_name(plugin.name):
                    log.warning("%s: %s", tool_dir, w)
            except Exception as exc:  # noqa: BLE001 — 非法插件跳过告警，不阻断
                log.warning("skip invalid tool plugin %s: %s", tool_dir, exc)
                continue
            found[plugin.name] = plugin
    return found


class PluginExecutor:
    """ToolExecutor over discovered plugins (adapter glue for the scheduler)."""

    def __init__(self, plugins: dict[str, ToolPlugin]) -> None:
        self._plugins = plugins

    def execute(self, capability: str, inputs: dict) -> Any:
        plugin = self._plugins.get(capability)
        if plugin is None:
            raise KeyError(f"no plugin for capability '{capability}'")
        return plugin.execute(inputs)
