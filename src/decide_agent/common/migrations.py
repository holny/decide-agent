"""Data migrations: meta.json version gate for evolved/ data (ARCHITECTURE §7).

meta.json {"version": N} lives at the data root (evolved/). Upgrade = apply
registered steps in order; missing/meta-less data is adopted at current version.
Steps are pure callables (store passes its connection/paths) and must be
idempotent — crashes between steps re-run them safely.
"""
import json
from collections.abc import Callable
from pathlib import Path

META_FILE = "meta.json"
CURRENT_VERSION = 1

# 迁移步骤注册表：{from_version: callable(ctx)}；ctx 由存储层提供（连接/路径）。
STEPS: dict[int, Callable[[object], None]] = {}


def register_step(from_version: int, step: Callable[[object], None]) -> None:
    STEPS[from_version] = step


def read_version(data_dir: Path) -> int:
    meta = Path(data_dir) / META_FILE
    if not meta.exists():
        return 0  # 未登记 → 采纳为当前版本并落 meta（首次初始化）
    try:
        return int(json.loads(meta.read_text("utf-8")).get("version", CURRENT_VERSION))
    except Exception:  # noqa: BLE001 — 损坏的 meta 视作待迁移
        return 0


def write_version(data_dir: Path, version: int) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / META_FILE).write_text(
        json.dumps({"version": version}, ensure_ascii=False), "utf-8",
    )


def migrate(data_dir: Path, ctx: object = None) -> int:
    """Bring data_dir to CURRENT_VERSION; returns final version."""
    data_dir = Path(data_dir)
    version = read_version(data_dir)
    while version < CURRENT_VERSION:
        step = STEPS.get(version)
        if step is not None:
            step(ctx)
        version += 1
    write_version(data_dir, CURRENT_VERSION)
    return version
