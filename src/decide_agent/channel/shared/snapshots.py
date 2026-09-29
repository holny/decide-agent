"""Decision snapshot store: runtime/decisions/{id}.json — 重启可恢复续答（红线 13）。"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

from decide_agent.schemas.workflow import DecisionOutcome


class DecisionSnapshotStore:
    def __init__(self, runtime_dir: Path) -> None:
        self._dir = Path(runtime_dir) / "decisions"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, outcome: DecisionOutcome, kernel_snapshot: dict) -> None:
        path = self._dir / f"{outcome.decision_id}.json"
        path.write_text(json.dumps({
            "outcome": outcome.model_dump(mode="json"),
            "kernel": kernel_snapshot,
        }, ensure_ascii=False), "utf-8")

    def load_all(self) -> list[dict]:
        snapshots = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                snapshots.append(json.loads(path.read_text("utf-8")))
            except Exception as exc:  # noqa: BLE001 — 损坏快照跳过（L4），不可恢复→expired 路径
                log.warning("skip corrupted snapshot %s: %s", path.name, exc)
                continue
        return snapshots

    def load_outcome(self, decision_id: str) -> DecisionOutcome | None:
        path = self._dir / f"{decision_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text("utf-8"))
        return DecisionOutcome.model_validate(data["outcome"])
