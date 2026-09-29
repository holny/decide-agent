"""P4-5 tests: 四层权重解析 / WeightModulation 生长 / 影子双跑 / 三指标。"""
import time

import pytest

from decide_agent.core.weights.modulation import (
    WeightModulationStore,
    attribute_feedback,
)
from decide_agent.core.weights.resolver import resolve
from decide_agent.experience.metrics import (
    ShadowRunner,
    agreement,
    coverage,
    drift,
    snapshot_metrics,
)
from decide_agent.schemas.judgment import JudgmentRecord
from decide_agent.schemas.question import TypedQuestion
from decide_agent.schemas.weights import ModulationSource, WeightModulation

NOW = 1_800_000_000.0
BASE = {"taste_match": 0.35, "distance": 0.2, "price": 0.2, "queue": 0.15, "weather_fit": 0.1}


def modulation(**kw) -> WeightModulation:
    params: dict = {"owner_id": "u1", "scene": "food", "dimension": "price",
                    "delta": 0.1, "updated_at": NOW}
    params.update(kw)
    return WeightModulation(**params)


# ------------------------------------------------------------------ resolver

def test_resolve_clamp_and_normalize():
    mods = [modulation(delta=0.5)]  # 0.2+0.5=0.7 → clamp 0.4（基线×2）
    eff = resolve("food", BASE, mods)
    assert eff.weights["price"] == pytest.approx(0.4 / 1.2, abs=1e-3)  # clamp 后归一化
    assert "price" in eff.modulated
    assert any("上调" in line for line in eff.disclosure)


def test_resolve_floor_clamp_and_scene_filter():
    mods = [
        modulation(delta=-0.5),                                # food：0.2-0.5 → floor 0.1
        modulation(scene="travel", delta=0.5),                 # 场景不符 → 忽略
        WeightModulation(owner_id="u1", scene="food", dimension="price",
                         delta=0.1, updated_at=NOW, status="retired"),  # retired → 忽略
    ]
    eff = resolve("food", BASE, mods)
    assert eff.weights["price"] == pytest.approx(0.1 / 0.9, abs=1e-3)
    assert eff.disclosure and "下调" in eff.disclosure[0]


def test_resolve_request_override_highest_priority():
    eff = resolve("food", BASE, [modulation(delta=0.5)], request_override={"price": 0.9})
    assert eff.weights["price"] == pytest.approx(0.9 / 1.7, abs=1e-3)  # 请求级不 clamp，参与归一化


# ------------------------------------------------------------------ modulation

def test_upsert_merges_evidence(tmp_path):
    store = WeightModulationStore(tmp_path, "u1")
    first = store.upsert(modulation(delta=0.1, source=ModulationSource.FEEDBACK))
    assert first.evidence_count == 1  # 新建即 count=1
    second = store.upsert(modulation(delta=0.2, source=ModulationSource.FEEDBACK))
    assert second.evidence_count == 2
    assert second.delta == 0.15  # 增量均值：0.1 + (0.2-0.1)/2 → 趋向观测均值（相同反馈不发散）
    assert second.strength == 1.0  # 封顶


def test_decay_and_retire(tmp_path):
    store = WeightModulationStore(tmp_path, "u1")
    store.upsert(modulation(strength=1.0, half_life_days=30))
    store.decay(now=NOW + 30 * 86400 * 20)  # 20 个半衰期 → 归零 retired
    items = store.all(active_only=False)
    assert items[0].status == "retired" and items[0].strength == 0.0


def test_attribute_feedback_top2_dimensions(tmp_path):
    store = WeightModulationStore(tmp_path, "u1")
    dims = [
        {"dimension": "taste_match", "score": 0.9, "weight": 0.35},
        {"dimension": "distance", "score": 0.8, "weight": 0.2},
        {"dimension": "price", "score": 0.3, "weight": 0.2},
    ]
    applied = attribute_feedback(store, "food", dims, accepted=True, now=NOW)
    assert {m.dimension for m in applied} == {"taste_match", "distance"}
    assert all(m.delta > 0 for m in applied)
    # 拒绝方向：独立 store 验证负增量（同 store 合并时 EMA 只渐变——设计语义）
    rejected = attribute_feedback(
        WeightModulationStore(tmp_path / "reject", "u1"), "food", dims,
        accepted=False, now=NOW)
    assert all(m.delta < 0 for m in rejected)


# ------------------------------------------------------------------ shadow + metrics

class StubProvider:
    def __init__(self, name, value, confidence=0.8) -> None:
        self.name = name
        self._value = value
        self._confidence = confidence

    def probe(self):
        from decide_agent.core.decision.availability import ProbeResult

        return ProbeResult()

    def answer(self, question):
        from decide_agent.schemas.question import TypedAnswer

        return TypedAnswer(shape=question.shape, value=self._value,
                           confidence=self._confidence, provider=self.name)


class StubChain:
    def __init__(self, providers) -> None:
        self.providers = providers


def test_shadow_runner_agreement():
    chain = StubChain([StubProvider("decision_model", 0.9), StubProvider("experience", 0.9)])
    result = ShadowRunner(chain).compare(TypedQuestion(shape="score", scene="food"))
    assert result["agree"] is True and result["scored"] == {"decision_model": 0.9, "experience": 0.9}

    chain2 = StubChain([StubProvider("decision_model", 0.9), StubProvider("experience", 0.3)])
    assert ShadowRunner(chain2).compare(TypedQuestion(shape="score", scene="food"))["agree"] is False


def _record(value, ts_iso) -> JudgmentRecord:
    return JudgmentRecord(
        ts=ts_iso, scene="food", shape="score", provider="experience", outcome="ok",
        question={"shape": "score"}, answer={"value": value, "basis": "命中口味"},
        confidence=0.65,
    )


def test_metrics_coverage_agreement_drift(tmp_path):
    now = time.time()
    hit = [ _record(0.9, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - i * 100))) for i in range(4)]
    miss = [ _record(0.5, time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - i * 100))) for i in range(4)]
    miss[0].answer["basis"] = None
    assert coverage(hit) == 1.0
    assert coverage(hit + miss) == 0.875  # 7/8 命中

    assert agreement([{"agree": True}, {"agree": False}, {"agree": True}]) == pytest.approx(2 / 3, abs=1e-3)

    import datetime
    iso = lambda epoch: datetime.datetime.fromtimestamp(epoch, datetime.UTC).isoformat()
    recent = [_record(0.9, iso(now - i * 100)) for i in range(5)]
    prior = [_record(0.3, iso(now - 8 * 86400 - i * 100)) for i in range(5)]
    assert drift(recent + prior, now=now) > 0.3

    data = snapshot_metrics(tmp_path / "metrics.json", coverage_v=1.0, agreement_v=0.9, drift_v=0.01)
    assert data["suggest_experience_mode"] is True  # 三指标阈值达标 → 建议切经验模式
