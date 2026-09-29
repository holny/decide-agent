"""Unified plugin manifest (P5/P6-1): scene_template | tool_server | mcp_server | presenter.

四类 kind 的目录契约与校验；非法跳过告警（不阻断主流程）。
搜索链 builtin < 用户 < 项目（同名覆盖），由各 discover* 实现复用 _iter_candidates。
"""
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PLUGIN_KINDS = ("scene_template", "tool_server", "mcp_server", "presenter")

# 各 kind 的必需文件
REQUIRED_FILES = {
    "scene_template": ["skill.jsonc", "dimensions.jsonc"],
    "tool_server": ["tool.jsonc", "impl.py"],
    "mcp_server": ["mcp.jsonc"],
    "presenter": ["presenter.jsonc", "impl.py"],
}


class PluginValidationError(ValueError):
    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path


def read_manifest(path: Path) -> dict[str, Any]:
    from decide_agent.common.jsonc import load as load_jsonc

    data = load_jsonc(path) or {}
    if data.get("kind") not in PLUGIN_KINDS:
        raise PluginValidationError(path, f"kind must be one of {PLUGIN_KINDS}")
    if not data.get("name"):
        raise PluginValidationError(path, "missing name")
    return data


def validate_plugin_dir(tool_dir: Path) -> dict[str, Any]:
    """目录 → manifest；缺失必需文件/字段即 PluginValidationError。"""
    manifest_path = next(
        (tool_dir / name for name in ("plugin.jsonc", "skill.jsonc", "tool.jsonc", "mcp.jsonc", "presenter.jsonc")
         if (tool_dir / name).exists()),
        None,
    )
    if manifest_path is None:
        raise PluginValidationError(tool_dir, "no manifest file")
    manifest = read_manifest(manifest_path)
    kind = manifest["kind"]
    missing = [name for name in REQUIRED_FILES.get(kind, []) if not (tool_dir / name).exists()]
    if missing:
        raise PluginValidationError(tool_dir, f"missing files for {kind}: {missing}")
    return manifest


def discover_plugin_dirs(search_dirs: list[Path], kind: str) -> dict[str, Path]:
    """按搜索链扫描（后目录覆盖同名），返回 name → 目录。非法跳过并告警。"""
    found: dict[str, Path] = {}
    for directory in search_dirs:
        if not Path(directory).exists():
            continue
        for child in sorted(Path(directory).iterdir()):
            if not child.is_dir():
                continue
            try:
                manifest = validate_plugin_dir(child)
            except PluginValidationError as exc:
                if any((child / name).exists() for name in
                       ("plugin.jsonc", "skill.jsonc", "tool.jsonc", "mcp.jsonc", "presenter.jsonc")):
                    log.warning("skip invalid plugin %s: %s", child, exc)
                continue
            if manifest["kind"] == kind:
                found[manifest["name"]] = child
    return found


# 标准能力名（内置场景模板 info_needs.yaml 引用的契约名）。
# 用户自定义工具若想覆盖内置实现，tool.jsonc 的 name 必须与之一致。
# 异名 = 新能力（共存，不覆盖）。
STANDARD_CAPABILITIES = {
    "location": "用户位置（lat/lng/city/district）",
    "weather": "天气（condition/temperature_c）",
    "poi_search": "候选搜索（返回候选列表）",
    "traffic": "交通状况",
    "deals": "优惠信息",
}


def validate_capability_name(name: str) -> list[str]:
    """检查工具名是否符合标准能力名规范；返回警告列表（不阻断）。"""
    warnings = []
    if name not in STANDARD_CAPABILITIES:
        similar = [k for k in STANDARD_CAPABILITIES if k in name or name in k]
        if similar:
            warnings.append(
                f"tool name '{name}' is similar to standard capability "
                f"'{similar[0]}' — use the exact standard name to override the built-in"
            )
    return warnings
