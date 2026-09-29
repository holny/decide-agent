"""Recorder unit tests: JSONL append, owner buckets, roundtrip."""
from decide_agent.experience.recorder import JudgmentRecorder
from decide_agent.schemas.judgment import JudgmentRecord


def _entry(provider="experience", outcome="ok") -> JudgmentRecord:
    return JudgmentRecord(
        ts="2026-09-24T00:00:00+00:00",
        scene="food",
        shape="score",
        provider=provider,
        outcome=outcome,
        question={"shape": "score"},
        answer={"shape": "score", "value": 0.9, "confidence": 0.8},
        confidence=0.8,
    )


def test_record_appends_jsonl(tmp_path):
    rec = JudgmentRecorder(base_dir=tmp_path)
    rec.record(_entry())
    rec.record(_entry(outcome="skipped"))
    lines = rec.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert rec.path == tmp_path / "default" / "judgments.jsonl"


def test_owner_bucketing(tmp_path):
    rec = JudgmentRecorder(base_dir=tmp_path)
    rec.record(_entry())
    assert (tmp_path / "default" / "judgments.jsonl").exists()
    other = JudgmentRecorder(base_dir=tmp_path, owner_id="u123")
    assert other.path == tmp_path / "u123" / "judgments.jsonl"
    other.record(_entry())
    assert len(rec.read_all()) == 1
    assert len(other.read_all()) == 1


def test_read_all_roundtrip(tmp_path):
    rec = JudgmentRecorder(base_dir=tmp_path)
    rec.record(_entry())
    loaded = rec.read_all()[0]
    assert loaded.provider == "experience"
    assert loaded.answer["value"] == 0.9
    assert loaded.degraded_from == []


def test_read_all_missing_bucket_is_empty(tmp_path):
    rec = JudgmentRecorder(base_dir=tmp_path)
    assert rec.read_all() == []
