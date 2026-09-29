"""Narrator: template rendering, zero LLM. Optional stage driven by config.output.format."""
from decide_agent.core.narrator.renderer import OutputFormat, build_reason, render

__all__ = ["OutputFormat", "build_reason", "render"]
