"""Convergence skeleton tests: bucketing, thresholds, filtering."""
from decide_agent.experience.converge import bucket_key, converge
from decide_agent.schemas.judgment import JudgmentRecord


def rec(value, candidate="蜀香居", dimension="口味", outcome="ok", shape="score") -> JudgmentRecord:
    answer = {"shape": shape, "provider": "experience"}
    if outcome == "ok":
        answer["value"] = value
    return JudgmentRecord(
        ts="2026-09-24T00:00:00+00:00", scene="food", shape=shape,
        provider="experience", outcome=outcome,
        question={"shape": shape, "candidate": candidate, "dimension": dimension},
        answer=answer if outcome == "ok" else None,
        confidence=0.65,
    )


def test_bucket_key_filtering():
    assert bucket_key(rec(0.8)) == "food|score|experience|蜀香居|口味"
    assert bucket_key(rec(0.8, outcome="failed")) is None
    assert bucket_key(rec("label", shape="classify")) is None


def test_convergence_thresholds():
    records = [rec(0.8), rec(0.8), rec(0.8), rec(0.8), rec(0.8)]  # 5 samples, sigma 0
    stats = converge(records)
    assert len(stats) == 1
    assert stats[0].converged and stats[0].n == 5 and stats[0].mean == 0.8

    two = converge([rec(0.8), rec(0.9)])
    assert not two[0].converged and two[0].n == 2

    spread = converge([rec(v) for v in (0.5, 0.9, 0.5, 0.9, 0.5)])  # sigma ~= 0.16 >= threshold
    assert not spread[0].converged


def test_failed_records_excluded():
    records = [rec(0.8), rec(0.8), rec(0.8), rec(0.8), rec(0.8, outcome="failed")]
    stats = converge(records)
    assert stats[0].n == 4 and not stats[0].converged
