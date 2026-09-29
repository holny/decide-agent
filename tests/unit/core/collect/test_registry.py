"""Capability registry tests."""
from decide_agent.core.collect.registry import CapabilityRegistry
from decide_agent.schemas.collect import Capability, Side


def test_register_get_override():
    reg = CapabilityRegistry()
    reg.register(Capability(name="location", side=Side.CLIENT))
    reg.register(Capability(name="weather", side=Side.SERVER))
    assert reg.has("location")
    assert reg.get("weather").side is Side.SERVER
    reg.register(Capability(name="weather", side=Side.MCP))  # 同名覆盖
    assert reg.get("weather").side is Side.MCP


def test_by_side_and_all():
    reg = CapabilityRegistry()
    reg.register(Capability(name="gps", side=Side.CLIENT))
    reg.register(Capability(name="ip_loc", side=Side.SERVER))
    reg.register(Capability(name="weather", side=Side.SERVER))
    assert [c.name for c in reg.by_side(Side.CLIENT)] == ["gps"]
    assert [c.name for c in reg.by_side(Side.SERVER)] == ["ip_loc", "weather"]  # 注册顺序
    assert reg.get("nope") is None
