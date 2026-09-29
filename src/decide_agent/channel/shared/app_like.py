"""DecisionAppLike：通道层对装配根的最小协议（依赖倒置，通道禁 import app）。

内核句柄仅约定 sweeper/快照/feedback 编排所需面；触达 core 内部细节仍属禁止。
"""
from typing import Any, Protocol

from decide_agent.schemas.collect import Capability
from decide_agent.schemas.workflow import DecisionOutcome, DecisionRequest


class KernelLike(Protocol):
    """内核句柄面（channel 侧可见的编排所需子集）。"""

    def snapshot(self, decision_id: str) -> dict: ...

    def restore(self, decision_id: str, snap: dict) -> None: ...

    def accept(self, decision_id: str) -> bool: ...

    def pending_item(self, request_id: str) -> Any: ...

    def expire_due(self, now: float | None = None) -> list[str]: ...


class DecisionAppLike(Protocol):
    def make_decision(self, request: DecisionRequest) -> DecisionOutcome: ...

    def respond(self, decision_id: str, request_id: str, response: dict) -> DecisionOutcome: ...

    def sweep(self, now: float | None = None) -> list[str]: ...

    def render(self, outcome: DecisionOutcome, fmt: str | None = None) -> dict | str: ...

    def list_capabilities(self) -> list[Capability]: ...

    @property
    def kernel(self) -> KernelLike: ...
