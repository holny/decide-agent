"""Unified logging: 结构化格式、级别控制、模块上下文。

用法（各模块）：
    from decide_agent.common.log import get_logger
    log = get_logger(__name__)
    log.warning("skip corrupted snapshot %s", path.name)

级别由 config `log.level` 控制（默认 WARNING；开发设 DEBUG）。
输出到 stderr（不污染 stdout 的 JSON 输出）。
"""
import logging
import sys

_FORMAT = "%(asctime)s [%(levelname).1s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_configured = False


def setup(level: str = "WARNING") -> None:
    """应用启动时调用一次（bootstrap），配置 decide-agent 根 logger。"""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    root = logging.getLogger("decide_agent")
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.WARNING))
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger；自动挂到 decide_agent 命名空间。"""
    full = name if name.startswith("decide_agent") else f"decide_agent.{name}"
    logger = logging.getLogger(full)
    if not _configured:
        setup()
    return logger
