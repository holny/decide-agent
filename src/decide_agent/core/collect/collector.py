"""TemplateCollector: info_needs tasks -> scheduler run -> candidates/env assembly.

装配约定（P1）：capability `poi_search` 的产出即候选列表；其余成功项进 env；
ask_once 失败项转为 A拍 needs（kernel 收集启动即问）。dim↔slot/输出绑定映射
由 P1-5+ 模板正式化（v4 §8 info_needs.jsonc）。
"""
from collections.abc import Callable
from pathlib import Path

from decide_agent.core.collect.scheduler import CollectScheduler
from decide_agent.schemas.collect import CollectorOutcome, CollectTask
from decide_agent.schemas.workflow import DecisionRequest

CANDIDATE_CAPABILITY = "poi_search"


def load_info_needs(scene: str, search_dirs: list[Path]) -> list[CollectTask]:
    """Read <dir>/<scene>/info_needs.jsonc v4 collect list (first hit wins)."""
    for directory in search_dirs:
        path = Path(directory) / scene / "info_needs.jsonc"
        if not path.exists():
            continue
        from decide_agent.common.jsonc import load as load_jsonc

        data = load_jsonc(path) or {}
        return [CollectTask.model_validate(item) for item in data.get("collect", [])]
    return []  # no template -> no collection (场景不需要外部信息 → 跳过收集)


class TemplateCollector:
    """Kernel Collector 协议实现（core 域内，同步）。"""

    def __init__(
        self,
        scheduler: CollectScheduler,
        tasks_loader: Callable[[str], list[CollectTask]],
        inputs_from: Callable[[DecisionRequest], dict] | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._tasks_loader = tasks_loader
        self._inputs_from = inputs_from or (lambda request: {"slots": request.slots, "env": request.env})

    def collect(
        self, scene: str, request: DecisionRequest, skip: set[str] | None = None,
    ) -> CollectorOutcome:
        tasks = self._tasks_loader(scene)
        if skip:
            tasks = [t for t in tasks if t.capability not in skip]
        if not tasks:
            return CollectorOutcome()  # 场景不需要外部信息 → 跳过收集（v4 跳过条件）
        inputs = {**self._inputs_from(request), "scene": scene}  # 场景上下文供工具按需取用
        base_env = inputs.get("env") or {}
        report = self._scheduler.run(tasks, inputs)
        outcome = CollectorOutcome(
            disclosed=report.disclosed, blocked=report.blocked,
        )
        poi = report.results.get(CANDIDATE_CAPABILITY)
        if poi is not None and poi.ok:
            outcome.candidates = poi.value or []
        for name, result in report.results.items():
            if not result.ok or name == CANDIDATE_CAPABILITY:
                continue
            task = next(t for t in tasks if t.capability == name)
            if task.bind:
                env_key, value = _bind(task.bind, result.value)
                if value is not None:
                    outcome.env[env_key] = value
            elif name not in base_env:  # 调用方已提供的 env 不被工具结果覆盖
                outcome.env[name] = result.value
        for need in report.needs:
            task = next(t for t in tasks if t.capability == need.capability)
            outcome.needs.append({
                "phase": "collect",
                "capability": need.capability,
                "slot": need.capability,
                "purpose": task.purpose,
                "question": {"text": f"请提供{task.purpose or need.capability}信息", "allow_free_text": True},
            })
        return outcome


def _bind(bind_path: str, value):
    """'weather.condition' -> ('weather', value['condition'])；路径缺失 -> (key, None)."""
    parts = bind_path.split(".")
    current = value
    for part in parts[1:]:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = None
    return parts[0], current
