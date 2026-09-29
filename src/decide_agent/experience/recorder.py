"""Judgment recorder: append-only JSONL, owner-bucketed.

Layout: <base_dir>/{owner_id}/judgments.jsonl — base_dir resolves to
evolved/users/ at assembly time (P1-9 bootstrap); tests inject tmp_path.
Implements core.decision.base.JudgmentSink structurally (no import needed:
core must stay free of domain cross-imports).
"""
from pathlib import Path

from decide_agent.schemas.judgment import JudgmentRecord


class JudgmentRecorder:
    def __init__(self, base_dir: Path, owner_id: str = "default") -> None:
        self._path = Path(base_dir) / owner_id / "judgments.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def record(self, entry: JudgmentRecord) -> None:
        """Append one line; create buckets on demand. Append-only, never rewrite."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def read_all(self, owner_id: str | None = None) -> list[JudgmentRecord]:
        """Load a bucket (P1-2 converge reads this); missing file -> empty list."""
        path = self._path if owner_id is None else self._path.parent.with_name(owner_id) / self._path.name
        if not path.exists():
            return []
        return [
            JudgmentRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
