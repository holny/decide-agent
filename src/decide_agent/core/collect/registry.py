"""Capability registry: four sides (server/mcp/client/memory), registration-order priority.

单能力单条目（P1）；多源同义能力（client GPS > server IP > memory）按注册顺序
优先取数的解析器在 P4 随真实数据源接入时落地（ARCHITECTURE §5.6）。
"""
from decide_agent.schemas.collect import Capability, Side


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        self._items[capability.name] = capability  # 后注册覆盖（同名覆盖语义）

    def get(self, name: str) -> Capability | None:
        return self._items.get(name)

    def has(self, name: str) -> bool:
        return name in self._items

    def by_side(self, side: Side) -> list[Capability]:
        return [c for c in self._items.values() if c.side is side]

    def all(self) -> list[Capability]:
        return list(self._items.values())
