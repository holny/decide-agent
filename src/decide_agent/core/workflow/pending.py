"""Pending requests: suspend/resume with decision_id+request_id double-key idempotency.

Duplicate responds to one request_id are deduplicated (first wins, replays return
the accepted record with accepted=False). Expiry is checked synchronously — the
channel-layer sweeper calls expire_due() periodically (ARCHITECTURE §5.4).
"""
import time

from decide_agent.common.ids import new_request_id
from decide_agent.schemas.error import DecisionError, ErrorSeverity, ErrorSource
from decide_agent.schemas.workflow import PendingKind, PendingRequest, PendingStatus


class PendingNotFoundError(KeyError):
    pass


class PendingExpiredError(RuntimeError):
    """过期挂起的 respond：返回 expired 结构化错误（红线 13），宿主侧重发新决策。"""

    def __init__(self, request_id: str) -> None:
        self.error = DecisionError(
            code="decision.expired",
            source=ErrorSource.EXPIRED,
            severity=ErrorSeverity.DEGRADABLE,
            detail=f"pending request {request_id} expired",
            degrade_path="宿主侧重发新决策",
        )
        super().__init__(self.error.disclose())


class PendingRegistry:
    def __init__(self) -> None:
        self._items: dict[str, PendingRequest] = {}

    def create(
        self,
        decision_id: str,
        kind: PendingKind,
        payload: dict,
        *,
        timeout_s: float | None = None,
        budget_cost: float = 0.0,
        clock: float | None = None,
    ) -> PendingRequest:
        item = PendingRequest(
            request_id=new_request_id(),
            decision_id=decision_id,
            kind=kind,
            payload=payload,
            timeout_s=timeout_s,
            budget_cost=budget_cost,
            created_ts=clock if clock is not None else time.time(),
        )
        self._items[item.request_id] = item
        return item

    def get(self, request_id: str) -> PendingRequest:
        item = self._items.get(request_id)
        if item is None:
            raise PendingNotFoundError(request_id)
        return item

    def respond(self, request_id: str, response: dict, *, clock: float | None = None) -> tuple[PendingRequest, bool]:
        """(item, accepted). Second+ respond -> (item, False) — idempotent dedup."""
        item = self.get(request_id)
        if item.status is not PendingStatus.PENDING:
            return item, False
        item.status = PendingStatus.ANSWERED
        item.response = response
        item.answered_ts = clock if clock is not None else time.time()
        return item, True

    def expire_due(self, now: float | None = None) -> list[str]:
        """Mark timed-out pendings expired; returns their ids (sweeper entry point)."""
        now_ts = now if now is not None else time.time()
        expired = [
            item.request_id for item in self._items.values()
            if item.status is PendingStatus.PENDING
            and item.timeout_s is not None
            and item.created_ts + item.timeout_s <= now_ts
        ]
        for rid in expired:
            self._items[rid].status = PendingStatus.EXPIRED
        return expired

    def restore(self, item: PendingRequest) -> None:
        """快照恢复（重启续答语义，红线 13）：按原 id/status 注册。"""
        self._items[item.request_id] = item

    def pending_for(self, decision_id: str) -> list[PendingRequest]:
        return [
            item for item in self._items.values()
            if item.decision_id == decision_id and item.status is PendingStatus.PENDING
        ]
