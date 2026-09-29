"""Runtime paths: XDG-style persistence directories (whole tree redirectable via config).

~/.local/share/decide-agent/
├── evolved/    Agent 唯一写区（记忆/判流水/权重/指标，owner 分区）
├── runtime/    易失：决策快照（decisions/）+ raw 卸载
└── raw/        工具大结果卸载区（raw_data_ref 指向处）
"""
import os
from dataclasses import dataclass
from pathlib import Path


def _xdg(kind: str, fallback: str) -> Path:
    env = os.environ.get(f"XDG_{kind}_HOME")
    base = Path(env).expanduser() if env else Path.home() / fallback
    return base / "decide-agent"


@dataclass(frozen=True)
class Paths:
    root: Path
    raw: Path

    @classmethod
    def resolve(cls, data_dir: str | os.PathLike | None = None) -> "Paths":
        root = Path(data_dir).expanduser() if data_dir else _xdg("DATA", ".local/share")
        paths = cls(root=root, raw=root / "raw")
        paths.root.mkdir(parents=True, exist_ok=True)
        return paths


def config_search_dirs() -> list[Path]:
    """Config lookup order: 项目级 ./decide-agent.yaml > 用户级 XDG_CONFIG > 内置默认。"""
    dirs = [Path.cwd(), _xdg("CONFIG", ".config")]
    return [d for d in dirs if d.exists()]


def user_config_file() -> Path:
    """用户配置；DECIDE_AGENT_USER_CONFIG 可隔离（demo/测试确定性）。"""
    override = os.environ.get("DECIDE_AGENT_USER_CONFIG")
    if override:
        return Path(override)
    xdg = _xdg("CONFIG", ".config")
    for name in ("config.jsonc", "config.json", "config.yaml"):  # 旧版 yaml 兼容读
        if (xdg / name).exists():
            return xdg / name
    return xdg / "config.jsonc"



def builtin_share_dirs() -> tuple[Path, Path]:
    """仓库三层制的 builtin 级目录解析（ARCHITECTURE §12.3）。

    开发态 = 仓库根 plugins/ 与 skills/；打包安装态 = site-packages 下的
    share/decide_agent/{plugins,skills}（hatch force-include 装入 wheel）。
    返回 (plugins_dir, skills_dir)。
    """
    env = os.environ.get("DECIDE_AGENT_BUILTIN_DIR")
    if env:
        root = Path(env)
        return root / "plugins", root / "skills"
    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "plugins").exists() and (repo_root / "skills").exists():
        return repo_root / "plugins", repo_root / "skills"
    installed = Path(__file__).resolve().parents[2] / "share" / "decide_agent"
    return installed / "plugins", installed / "skills"
