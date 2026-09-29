"""Scheduler tests: DAG batches, degrade policies, dependency root-cause attribution."""
import pytest

from decide_agent.core.collect.registry import CapabilityRegistry
from decide_agent.core.collect.scheduler import CircularDependency, CollectScheduler
from decide_agent.schemas.collect import Capability, CollectTask, DegradePolicy


class FakeExecutor:
    def __init__(self, results: dict) -> None:
        self._results = results

    def execute(self, capability: str, inputs: dict):
        result = self._results[capability]
        if isinstance(result, Exception):
            raise result
        return result


def registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    for name in ("location", "weather", "poi_search", "traffic"):
        reg.register(Capability(name=name, side="server"))
    return reg


def scheduler(results: dict) -> CollectScheduler:
    return CollectScheduler(registry(), FakeExecutor(results))


TASKS = [
    CollectTask(capability="location", fallback=DegradePolicy.ASK_ONCE),
    CollectTask(capability="weather", fallback=DegradePolicy.NEUTRAL),
    CollectTask(capability="poi_search", fallback=DegradePolicy.BLOCK, depends_on=["location"]),
    CollectTask(capability="traffic", fallback=DegradePolicy.DEGRADE),
]


def test_plan_topology_batches():
    batches = CollectScheduler.plan(TASKS)
    assert [[t.capability for t in b] for b in batches] == [
        ["location", "weather", "traffic"], ["poi_search"],
    ]


def test_plan_circular_raises():
    tasks = [
        CollectTask(capability="a", depends_on=["b"]),
        CollectTask(capability="b", depends_on=["a"]),
    ]
    with pytest.raises(CircularDependency):
        CollectScheduler.plan(tasks)


def test_run_all_ok():
    report = scheduler({
        "location": {"lat": 31.19}, "weather": {"condition": "小雨"},
        "poi_search": [{"id": "a"}], "traffic": {"level": "畅通"},
    }).run(TASKS)
    assert report.blocked is False and report.needs == []
    assert report.results["location"].ok and report.results["weather"].ok
    assert report.results["poi_search"].value == [{"id": "a"}]


def test_ask_once_failure_surfaces_need():
    report = scheduler({
        "location": RuntimeError("no GPS"), "weather": {"condition": "小雨"},
        "poi_search": [{"id": "a"}], "traffic": {"level": "畅通"},
    }).run(TASKS)
    assert not report.results["location"].ok
    assert report.results["location"].needs_ask is True
    assert len(report.needs) == 1


def test_neutral_and_degrade_caveats():
    report = scheduler({
        "location": {"lat": 1}, "weather": RuntimeError("weather api down"),
        "poi_search": [{"id": "a"}], "traffic": RuntimeError("traffic down"),
    }).run(TASKS)
    assert not report.results["weather"].ok
    assert "neutral" in report.results["weather"].caveat
    assert "degrade" in report.results["traffic"].caveat
    assert any("weather" in line for line in report.disclosed)


def test_dependency_failure_blocks_with_root_cause():
    report = scheduler({
        "location": RuntimeError("no GPS"), "weather": {"condition": "x"},
        "traffic": {"level": "x"}, "poi_search": [{"id": "a"}],
    }).run(TASKS)
    poi = report.results["poi_search"]
    assert not poi.ok
    assert "dependency not ok: location" in poi.error["detail"]  # 归因到根节点
    assert report.blocked is True  # poi_search fallback=block -> 唯一硬结局


def test_unregistered_capability_degrades():
    tasks = [CollectTask(capability="deals", fallback=DegradePolicy.NEUTRAL)]
    report = scheduler({}).run(tasks)
    assert not report.results["deals"].ok
    assert "not registered" in report.results["deals"].caveat
