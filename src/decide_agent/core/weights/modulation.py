"""WeightModulation 生长通道与存储（evolved/users/{owner}/weights/modulations.json）。

生长通道（v4 §5.5）：declare 显式声明 / feedback 接受拒绝归因 / shadow 影子对照。
同 (scene, dimension, source) 合并：evidence_count+1，delta 按计数渐近（防单次跳变）。
"""
import json
import time
from pathlib import Path

from decide_agent.schemas.weights import ModulationSource, WeightModulation

FEEDBACK_DELTA = 0.02  # 单次反馈的基准增量（可配）


class WeightModulationStore:
    def __init__(self, evolved_dir: Path, owner_id: str) -> None:
        self._path = Path(evolved_dir) / "users" / owner_id / "weights" / "modulations.json"
        self._owner = owner_id

    def _load(self) -> list[WeightModulation]:
        if not self._path.exists():
            return []
        return [WeightModulation.model_validate(m) for m in json.loads(self._path.read_text("utf-8"))]

    def _save(self, items: list[WeightModulation]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([m.model_dump(mode="json") for m in items], ensure_ascii=False, indent=1),
            "utf-8",
        )

    def all(self, scene: str | None = None, active_only: bool = True) -> list[WeightModulation]:
        items = self._load()
        if scene:
            items = [m for m in items if m.scene == scene]
        if active_only:
            items = [m for m in items if m.status == "active"]
        return items

    def upsert(self, modulation: WeightModulation) -> WeightModulation:
        items = self._load()
        for index, existing in enumerate(items):
            if (existing.scene, existing.dimension, existing.source.value) == \
                    (modulation.scene, modulation.dimension, modulation.source.value):
                merged = existing.model_copy(update={
                    "evidence_count": existing.evidence_count + 1,
                    "delta": round(existing.delta + (modulation.delta - existing.delta) / (existing.evidence_count + 1), 4),
                    "strength": min(1.0, existing.strength + 0.1),
                    "updated_at": modulation.updated_at,
                })
                items[index] = merged
                self._save(items)
                return merged
        stored = modulation.model_copy(update={"evidence_count": 1})
        items.append(stored)
        self._save(items)
        return stored

    def decay(self, now: float) -> int:
        """半衰期衰减强度；强度归零 retired。"""
        items = self._load()
        changed = 0
        for m in items:
            age_days = max(0.0, (now - m.updated_at) / 86400.0)
            strength = m.strength * (0.5 ** (age_days / m.half_life_days))
            if strength < 0.01:
                m.status = "retired"
                m.strength = 0.0
            else:
                m.strength = round(strength, 4)
            changed += 1
        self._save(items)
        return changed

    def retire(self, scene: str, dimension: str, source: ModulationSource) -> bool:
        items = self._load()
        for m in items:
            if (m.scene, m.dimension, m.source.value) == (scene, dimension, source.value):
                m.status = "retired"
                self._save(items)
                return True
        return False


def attribute_feedback(
    store: WeightModulationStore,
    scene: str,
    dimension_scores: list[dict],
    accepted: bool,
    now: float | None = None,
) -> list[WeightModulation]:
    """接受/拒绝归因到维度（v4 §5.5b）：top-2 加权维度获得 ±FEEDBACK_DELTA。"""
    now = now if now is not None else time.time()
    ranked = sorted(dimension_scores, key=lambda d: d.get("score", 0) * d.get("weight", 0), reverse=True)
    applied = []
    for ds in ranked[:2]:
        dimension = ds.get("dimension")
        if not dimension:
            continue
        delta = FEEDBACK_DELTA if accepted else -FEEDBACK_DELTA
        applied.append(store.upsert(WeightModulation(
            owner_id=store._owner, scene=scene, dimension=dimension,
            delta=delta, source=ModulationSource.FEEDBACK,
            updated_at=now,
        )))
    return applied