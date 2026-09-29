"""PendingSweeper: background expiry sweep + EXPIRED event push (通道层职责，§5.4).

core 保持同步纯逻辑：sweeper 周期调用 app.sweep()（内核同步检查方法），
过期项补发 expired 事件供 SSE 主动推送；respond 时惰性校验兜底。
"""
import threading

from decide_agent.channel.shared.app_like import DecisionAppLike
from decide_agent.channel.shared.events import EventStore, EventType


class PendingSweeper:
    def __init__(self, app: DecisionAppLike, events: EventStore, interval_s: float = 1.0) -> None:
        self._app = app
        self._events = events
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "PendingSweeper":
        self._thread = threading.Thread(target=self._loop, name="pending-sweeper", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.sweep_once()

    def sweep_once(self) -> list[str]:
        expired = self._app.sweep()
        for request_id in expired:
            try:
                item = self._app.kernel.pending.get(request_id)
            except KeyError:  # pragma: no cover — 刚被清理
                continue
            self._events.append(
                item.decision_id, EventType.EXPIRED,
                {"request_id": request_id, "code": "decision.expired",
                 "degrade_path": "respond 返回 expired 结构化错误，宿主侧重发新决策"},
            )
        return expired
