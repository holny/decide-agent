"""美食场景收集全链集成：discovery → registry → scheduler → template collector → kernel。

P1-5 验收：美食场景收集走新调度器；A拍阻塞缺口挂起-应答-重收集闭环。
"""
from pathlib import Path

from decide_agent.core.collect.collector import TemplateCollector, load_info_needs
from decide_agent.core.collect.registry import CapabilityRegistry
from decide_agent.core.collect.scheduler import CollectScheduler
from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.plugins.discovery import PluginExecutor, discover_tools
from decide_agent.schemas.collect import Capability, CollectorOutcome
from decide_agent.schemas.workflow import DecisionRequest

ROOT = Path(__file__).resolve().parents[2]
REPO_SKILLS = ROOT / "skills"
REPO_PLUGINS = ROOT / "plugins" / "tools"

WEIGHTS = {
    "taste_match": 0.35, "distance": 0.20, "price": 0.20, "queue": 0.15, "weather_fit": 0.10,
}


def template_collector() -> TemplateCollector:
    registry = CapabilityRegistry()
    for name, plugin in discover_tools([REPO_PLUGINS]).items():
        registry.register(Capability(name=name, side=plugin.side, description=plugin.description))
    scheduler = CollectScheduler(registry, PluginExecutor(discover_tools([REPO_PLUGINS])))
    return TemplateCollector(scheduler, lambda scene: load_info_needs(scene, [REPO_SKILLS]))


def test_food_collect_full_chain_produces_candidates_and_env():
    outcome = template_collector().collect("food", DecisionRequest(question="想吃辣"))
    assert len(outcome.candidates) == 8
    assert outcome.candidates[0]["name"] == "蜀香居"
    assert isinstance(outcome.env.get("weather"), str) and outcome.env["weather"]  # bind: weather.condition（真实天气）
    assert not outcome.blocked
    # traffic/deals 未注册 mock → 降级 caveat 披露
    assert any("traffic" in line for line in outcome.disclosed)
    assert any("deals" in line for line in outcome.disclosed)


def test_travel_collect_returns_attractions_not_restaurants():
    """场景回归：travel 的 poi_search 必须返回景点（scene 经 inputs 透传给插件）。"""
    outcome = template_collector().collect("travel", DecisionRequest(question="周末去哪玩"))
    assert outcome.candidates, "travel collect 应产出候选"
    assert all(c["category"] == "attraction" for c in outcome.candidates)
    assert all("蜀香居" != c["name"] for c in outcome.candidates)
    assert all("crowd_index" in c for c in outcome.candidates)  # travel rules 的 crowd 维度字段


def test_kernel_with_collector_end_to_end():
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    kernel = DecisionKernel(engine, collector=template_collector())
    outcome = kernel.make_decision(DecisionRequest(
        question="想吃辣，预算100", scene_hint="food",
        slots={"taste_match": "辣"}, weights=WEIGHTS,
    ))
    assert outcome.status == "completed"
    # 辣妹子炒菜：比蜀香居更近(750m vs 850m)更便宜(50 vs 65)排队更短 —— 胜出合理
    # （P0 的 dist_km*100 距离公式使蜀香居以 85m 胜出，属 P0 mock 数据缺陷，未沿用）
    assert outcome.result.recommendation.candidate.id == "poi_004"
    assert outcome.result.recommendation.total_score > 0.7
    assert any("traffic" in line for line in outcome.missing_information)


def test_kernel_ask_once_loop(tmp_path):
    """A拍：location 缺失 → 收集启动即问 → respond 补充 → 重收集完成。"""

    class StubCollector:
        def __init__(self) -> None:
            self.calls = 0

        def collect(self, scene: str, request: DecisionRequest, skip=None) -> CollectorOutcome:
            self.calls += 1
            if "location" not in request.slots:
                return CollectorOutcome(
                    needs=[{"phase": "collect", "capability": "location",
                            "slot": "location",
                            "question": {"text": "方便告诉我你的位置吗？"}}],
                    disclosed=["location 缺失，已追问"],
                )
            return CollectorOutcome(
                candidates=[{"id": "poi_000", "name": "蜀香居", "tags": ["辣"],
                             "distance_m": 500, "price_per_person": 65, "wait_min": 10}],
                env={"weather": "小雨"},
            )

    collector = StubCollector()
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    kernel = DecisionKernel(engine, collector=collector, gate_threshold=0.7)
    outcome = kernel.make_decision(DecisionRequest(
        question="想吃辣", scene_hint="food", weights=WEIGHTS,
        interactive=True, slots={"taste_match": "辣"},
    ))
    assert outcome.status == "require_action"  # A拍：收集启动即问
    assert outcome.pending.payload["capability"] == "location"
    assert outcome.pending.payload["phase"] == "collect"

    final = kernel.respond(outcome.decision_id, outcome.pending.request_id,
                           {"value": "徐家汇"})
    assert collector.calls == 2  # respond 触发重收集
    assert final.status == "completed"
    assert final.result.recommendation.candidate.id == "poi_000"
    assert final.result.recommendation.total_score > 0.5
