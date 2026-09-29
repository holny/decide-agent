"""EventStore: per-decision in-process event log with seq + after_seq replay.

内核同步零事件概念——事件由通道层在调用边界追加（kernel outcome → events），
SSE 断线重连按 after_seq 重放。进程内存储；跨进程持久化属 P3+ 会话快照范畴。
"""
import threading

from decide_agent.schemas.events import EventEnvelope, EventType


class EventStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: dict[str, list[EventEnvelope]] = {}

    def append(
        self, decision_id: str, event_type: EventType, payload: dict | None = None, *,
        ts: float | None = None,
    ) -> EventEnvelope:
        import time

        with self._lock:
            log = self._logs.setdefault(decision_id, [])
            envelope = EventEnvelope(
                decision_id=decision_id,
                seq=len(log),
                type=event_type,
                payload=payload or {},
                ts=ts if ts is not None else time.time(),
            )
            log.append(envelope)
            return envelope

    def replay(self, decision_id: str, after_seq: int = -1) -> list[EventEnvelope]:
        """断线续传：返回 seq > after_seq 的事件（含序）。"""
        with self._lock:
            log = self._logs.get(decision_id, [])
            return [e for e in log if e.seq > after_seq]

    def last_seq(self, decision_id: str) -> int:
        with self._lock:
            log = self._logs.get(decision_id, [])
            return log[-1].seq if log else -1
