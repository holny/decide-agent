"""CollectScheduler: task list -> DAG topo batches -> execute -> degrade policies.

清单 = 模板 collect ∪ 意图 extra_needs ∪ 缺口反哺（增量补采，P4+）。
调度是纯代码：拓扑批次内 P1 顺序执行（批次结构披露，供装配层并发执行器复用）；
失败沿依赖链归因到根节点；降级四策略 ask_once/degrade/neutral/block（§5.6）。
本模块零智能判断。
"""
from typing import Any, Protocol

from decide_agent.schemas.collect import CollectReport, CollectResult, CollectTask, DegradePolicy


class ToolExecutor(Protocol):
    """实际取数执行器（插件能力），raise 即失败。"""

    def execute(self, capability: str, inputs: dict) -> Any: ...


class CircularDependency(ValueError):
    pass


class CollectScheduler:
    def __init__(self, registry, executor: ToolExecutor) -> None:
        self._registry = registry
        self._executor = executor

    @staticmethod
    def plan(tasks: list[CollectTask]) -> list[list[CollectTask]]:
        """Kahn 层级：最大并行批次（依赖就绪的同一层）。"""
        done: set[str] = set()
        remaining = list(tasks)
        batches: list[list[CollectTask]] = []
        while remaining:
            level = [t for t in remaining if all(d in done for d in t.depends_on)]
            if not level:
                raise CircularDependency(f"unresolvable dependencies in {[t.capability for t in remaining]}")
            batches.append(level)
            done |= {t.capability for t in level}
            remaining = [t for t in remaining if t not in level]
        return batches

    def run(self, tasks: list[CollectTask], inputs: dict | None = None) -> CollectReport:
        inputs = inputs or {}
        report = CollectReport(batches=[[t.capability for t in level] for level in self.plan(tasks)])
        done_values: dict[str, Any] = {}
        ok_caps: set[str] = set()
        for level in self.plan(tasks):
            for task in level:
                result = self._run_one(task, inputs, ok_caps, done_values)
                report.results[task.capability] = result
                if result.ok:
                    done_values[task.capability] = result.value
                    ok_caps.add(task.capability)
                if result.needs_ask:
                    report.needs.append(result)
                if result.caveat:
                    report.disclosed.append(result.caveat)
                if task.fallback is DegradePolicy.BLOCK and not result.ok:
                    report.blocked = True
        return report

    def _run_one(self, task: CollectTask, inputs: dict, ok_caps: set[str], done_values: dict) -> CollectResult:
        capability = task.capability
        if not self._registry.has(capability):
            return self._degrade(task, reason=f"capability '{capability}' not registered")
        unmet = [d for d in task.depends_on if d not in ok_caps]
        if unmet:
            # 失败沿依赖链归因到根节点
            return self._degrade(task, reason=f"dependency not ok: {','.join(unmet)}")
        try:
            # 依赖产物注入 env（如 location → poi_search 的用户坐标）；调用方已有值不覆盖
            base_env = inputs.get("env") or {}
            dep_env = {dep: done_values[dep] for dep in task.depends_on
                       if dep in done_values and dep not in base_env}
            task_inputs = inputs
            if dep_env:
                task_inputs = {**inputs, "env": {**base_env, **dep_env}}
            value = self._executor.execute(capability, task_inputs)
            return CollectResult(capability=capability, ok=True, value=value)
        except Exception as exc:  # noqa: BLE001 — tool failure is a gap, never a crash (L4)
            return self._degrade(task, reason=f"{type(exc).__name__}: {exc}")

    def _degrade(self, task: CollectTask, *, reason: str) -> CollectResult:
        base = CollectResult(
            capability=task.capability,
            error={"detail": reason, "policy": task.fallback.value},
        )
        if task.fallback is DegradePolicy.ASK_ONCE:
            base.needs_ask = True
            base.caveat = f"{task.capability} 缺失，已生成追问"
        elif task.fallback is DegradePolicy.BLOCK:
            base.caveat = f"{task.capability} 失败且不可降级（block_to_clarify）：{reason}"
        else:  # DEGRADE / NEUTRAL：打分层把缺失维度按权重置零/中性处理
            base.caveat = f"{task.capability} 不可用（{task.fallback.value}）：{reason}"
        return base
