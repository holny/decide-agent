"""Kernel snapshot/restore tests: 挂起→快照→新内核恢复→续答完成（红线 13）。"""
from pathlib import Path

from decide_agent.core.decision.chain import ProviderChain
from decide_agent.core.decision.engine import DecisionEngine
from decide_agent.core.workflow.kernel import DecisionKernel
from decide_agent.experience.provider import ExperienceProvider
from decide_agent.schemas.workflow import DecisionRequest

REPO_SKILLS = Path(__file__).resolve().parents[4] / "skills"
WEIGHTS = {
    "taste_match": 0.35, "distance": 0.20, "price": 0.20, "queue": 0.15, "weather_fit": 0.10,
}
CANDIDATES = [
    {"id": "a", "name": "蜀香居", "tags": ["辣"], "distance_m": 500, "price_per_person": 65, "wait_min": 10},
    {"id": "b", "name": "清淡居", "tags": ["清淡"], "distance_m": 300, "price_per_person": 30, "wait_min": 5},
]


def kernel() -> DecisionKernel:
    engine = DecisionEngine(ProviderChain([ExperienceProvider(search_dirs=[REPO_SKILLS])]))
    return DecisionKernel(engine, weights_resolver=lambda s: WEIGHTS, gate_threshold=0.7)


def test_snapshot_restore_resume(tmp_path):
    first = kernel()
    asked = first.make_decision(DecisionRequest(
        question="想吃辣", scene_hint="food", interactive=True, slots={}, candidates=CANDIDATES,
    ))
    assert asked.status == "require_action"
    snap = first.snapshot(asked.decision_id)

    # 进程重启语义：全新内核实例恢复快照后可续答
    second = kernel()
    second.restore(asked.decision_id, snap)
    final = second.respond(asked.decision_id, asked.pending.request_id, {"value": "辣"})
    assert final.status == "completed"
    assert final.result.recommendation.candidate.id == "a"
    assert final.result.recommendation.total_score > 0.5


def test_snapshot_unknown_decision_raises():
    import pytest

    with pytest.raises(KeyError):
        kernel().snapshot("d-nope")


def test_accept_completes(tmp_path):
    k = kernel()
    outcome = k.make_decision(DecisionRequest(
        question="想吃辣", scene_hint="food", slots={"taste_match": "辣"}, candidates=CANDIDATES,
    ))
    assert outcome.status == "completed" and outcome.state == "suggested"
    assert k.accept(outcome.decision_id) is True
    snap = k.snapshot(outcome.decision_id)
    assert snap["state"] == "completed"
