"""Adapter: plugin loading mechanism (manifest/discovery/loader) — not plugin content."""
from decide_agent.plugins.manifest import (
    PLUGIN_KINDS,
    PluginValidationError,
    discover_plugin_dirs,
    validate_plugin_dir,
)

__all__ = ["PLUGIN_KINDS", "PluginValidationError", "discover_plugin_dirs", "validate_plugin_dir"]
