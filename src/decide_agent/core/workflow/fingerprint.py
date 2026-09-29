"""Stage fingerprint: hash of a stage's input contract.

Identical stage inputs -> skip & reuse the cached stage output; any change
recomputes that stage (and downstream naturally, since their payloads embed
upstream outputs). Granularity note: score-stage payloads currently cover the
whole input bundle, so a respond() re-scores all dims; per-dimension minimal
re-scoring ("只重算 queue") lands with template input-mapping in P1-5.
"""
import hashlib
import json
from typing import Any


def _hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class StageFingerprint:
    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}
        self.hits: list[str] = []

    def should_skip(self, stage: str, payload: Any) -> bool:
        """True when the exact stage inputs were seen before (caller reuses output)."""
        digest = _hash(payload)
        if self._hashes.get(stage) == digest:
            self.hits.append(stage)
            return True
        return False

    def update(self, stage: str, payload: Any) -> str:
        digest = _hash(payload)
        self._hashes[stage] = digest
        return digest

    def restore(self, hashes: dict[str, str]) -> None:
        """快照恢复：继续上次的阶段指纹（score 缓存不恢复→首调重算并更新）。"""
        self._hashes.update(hashes)
