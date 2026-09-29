"""Integration: kernel respond (scope=always/session) → memory service → 落自有层。

P2-5 acceptance: learned_memory 双向（outcome 披露 + owner 层持久化）；once 不落库。
"""
from pathlib import Path

from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.memory.manager import MemoryService, build_memory_store
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.schemas.workflow import DecisionRequest

ROOT = Path(__file__).resolve().parents[2]
REPO_SKILLS = ROOT / "skills"
WEIGHTS = {
    "taste_match": 0.35, "distance": 0.20, "price": 0.20, "queue": 0.15, "weather_fit": 0.10,
}
CANDIDATES = [
    {"id": "a", "name": "蜀香居", "tags": ["辣"], "distance_m": 500, "price_per_person": 65, "wait_min": 10},
    {"id": "b", "name": "清淡居", "tags": ["清淡"], "distance_m": 300, "price_per_person": 30, "wait_min": 5},
]


def kernel(tmp_path, **kw) -> DecisionKernel:
    memory = MemoryService(build_memory_store(tmp_path / "evolved", "u1"), raw_dir=tmp_path / "raw")
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    return DecisionKernel(
        engine, memory=memory, owner_id="u1",
        weights_resolver=lambda scene: WEIGHTS, gate_threshold=0.7, **kw,
    )


def test_scope_always_writes_memory(tmp_path):
    k = kernel(tmp_path)
    asked = k.make_decision(DecisionRequest(
        question="想吃辣", scene_hint="food", interactive=True, slots={}, candidates=CANDIDATES,
    ))
    assert asked.status == "require_action"  # taste 缺失 → B拍提问
    final = k.respond(asked.decision_id, asked.pending.request_id,
                      {"value": "辣", "scope": "always"})
    assert final.status == "completed"
    assert final.learned_memory, "scope=always 应答应产出 learned_memory"
    assert final.learned_memory[0]["content"] == "taste_match:辣"

    # 双向验证：新 kernel 实例（重启语义）从 owner 层读回
    fresh_store = build_memory_store(tmp_path / "evolved", "u1")
    stored = MemoryService(fresh_store).export("u1")
    assert any(r.content == "taste_match:辣" and r.source == "feedback" for r in stored)


def test_scope_once_does_not_persist(tmp_path):
    k = kernel(tmp_path)
    asked = k.make_decision(DecisionRequest(
        question="想吃辣", scene_hint="food", interactive=True, slots={}, candidates=CANDIDATES,
    ))
    final = k.respond(asked.decision_id, asked.pending.request_id,
                      {"value": "辣", "scope": "once"})
    assert final.status == "completed"
    assert final.learned_memory == []  # once 只作用于本次
    assert MemoryService(build_memory_store(tmp_path / "evolved", "u1")).export("u1") == []
