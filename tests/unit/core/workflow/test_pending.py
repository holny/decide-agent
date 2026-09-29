"""Pending tests: suspend/resume, double-key idempotency, expiry sweep."""
import pytest

from decide_agent.core.workflow.pending import PendingNotFoundError, PendingRegistry
from decide_agent.schemas.workflow import PendingKind, PendingStatus


def test_create_and_respond_once():
    reg = PendingRegistry()
    item = reg.create("d1", PendingKind.QUESTION, {"dimension": "taste"}, timeout_s=10, clock=100.0)
    assert item.status is PendingStatus.PENDING
    answered, accepted = reg.respond(item.request_id, {"value": "辣"}, clock=101.0)
    assert accepted and answered.status is PendingStatus.ANSWERED
    assert answered.response == {"value": "辣"}


def test_duplicate_respond_is_deduplicated():
    reg = PendingRegistry()
    item = reg.create("d1", PendingKind.QUESTION, {}, clock=100.0)
    reg.respond(item.request_id, {"value": "first"})
    again, accepted = reg.respond(item.request_id, {"value": "second"})
    assert accepted is False  # 幂等去重：首次应答生效
    assert again.response == {"value": "first"}


def test_unknown_request_raises():
    reg = PendingRegistry()
    with pytest.raises(PendingNotFoundError):
        reg.respond("nope", {})


def test_expiry_sweep_and_late_respond():
    reg = PendingRegistry()
    item = reg.create("d1", PendingKind.TOOL, {"tool": "geo"}, timeout_s=5, clock=100.0)
    assert reg.expire_due(now=104.0) == []  # not yet due
    assert reg.expire_due(now=105.0) == [item.request_id]
    assert item.status is PendingStatus.EXPIRED
    assert reg.pending_for("d1") == []  # expired no longer pending
    _, accepted = reg.respond(item.request_id, {"late": True})
    assert accepted is False  # expired -> 降级顺延（kernel 不合并该应答）


def test_pending_for_filters_by_decision():
    reg = PendingRegistry()
    a = reg.create("dA", PendingKind.QUESTION, {}, clock=1.0)
    reg.create("dB", PendingKind.TOOL, {}, clock=1.0)
    assert [p.request_id for p in reg.pending_for("dA")] == [a.request_id]
