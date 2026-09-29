"""Three-source merge: external > session > user, with conflict observation (v4 §6).

冲突不静默覆盖自有条目：incoming 与长期记忆同 (kind, scene) 且内容不同 → 长期条目
降权挂起 + 观察计数；一致观察 ≥ CONFIRM_THRESHOLD 次才允许更新（防宿主临时事实污染长期）。
"""
from dataclasses import dataclass, field

from decide_agent.core.memory import policy
from decide_agent.schemas.memory import MemoryRecord, MemoryStatus


@dataclass
class ConflictState:
    """会话内冲突观察（进程内即可；持久化随 P3 会话快照）。"""

    key: str
    incoming_content: str
    count: int = 0


@dataclass
class MergeReport:
    records: list[MemoryRecord] = field(default_factory=list)  # 生效视图（外部影子在前）
    conflicts: list[ConflictState] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def conflict_key(record: MemoryRecord) -> str:
    return f"{record.kind.value}:{record.scene}:{record.owner_id}"


class SourceMerger:
    def __init__(self, confirm_threshold: int = policy.CONFIRM_THRESHOLD) -> None:
        self._threshold = confirm_threshold
        self._observations: dict[str, ConflictState] = {}

    def observe(self, user_record: MemoryRecord, incoming: MemoryRecord) -> bool:
        """登记一次外部/会话观察。返回 True = 已达阈值、允许更新长期条目。"""
        key = f"{conflict_key(user_record)}:{incoming.content}"
        state = self._observations.get(key)
        if state is None:
            state = ConflictState(key=key, incoming_content=incoming.content)
            self._observations[key] = state
        state.count += 1
        return state.count >= self._threshold

    def merge(
        self,
        user_records: list[MemoryRecord],
        session_records: list[MemoryRecord],
        external_records: list[MemoryRecord],
        now: float,
    ) -> MergeReport:
        """生效视图：外部 > 会话 > 用户（同 kind+scene 冲突时高优先级源遮蔽低优先级）。"""
        report = MergeReport()
        seen_keys: set[tuple] = set()
        for source_name, records in (
            ("external", external_records),
            ("session", session_records),
            ("user", user_records),
        ):
            for record in records:
                key = (record.kind, record.scene, _normalized(record.content))
                if key in seen_keys:
                    continue  # 高优先级源已覆盖
                seen_keys.add(key)
                report.records.append(record)
                policy.touch(record, now)

        # 冲突观察：与外部不同的用户长期条目 → 挂起降权（不静默覆盖）
        external_contents = {_normalized(r.content) for r in external_records}
        for user_record in user_records:
            external_peer = _peer(external_records, user_record)
            if not external_peer or _normalized(user_record.content) in external_contents:
                continue
            state_key = f"{conflict_key(user_record)}:{external_peer.content}"
            state = self._observations.get(state_key)
            if state is None:
                state = ConflictState(key=state_key, incoming_content=external_peer.content)
                self._observations[state_key] = state
            state.count += 1
            if user_record.status is MemoryStatus.ACTIVE:
                policy.demote_on_conflict(user_record)  # 挂起 + 置信减半（仅首次）
            report.conflicts.append(state)
            reached = "已达阈值，可更新" if state.count >= self._threshold else ""
            report.caveats.append(
                f"记忆冲突（{user_record.content} ← {external_peer.content}）：已挂起待确认"
                f"（观察 {state.count}/{self._threshold}{'，' + reached if reached else ''}）",
            )
        return report


def _normalized(content: str) -> str:
    return "".join(content.split()).lower()


def _peer(records: list[MemoryRecord], user_record: MemoryRecord) -> MemoryRecord | None:
    """外部源中同 (kind, scene) 的条目（内容不同才算冲突）。"""
    for record in records:
        if record.kind is user_record.kind and record.scene == user_record.scene \
                and _normalized(record.content) != _normalized(user_record.content):
            return record
    return None
