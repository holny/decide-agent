"""Chain unit tests: probe-skip order, mid-run failure injection, L4 never-raise, sink trail."""
from decide_agent.core.decision.availability import Availability, ProbeResult
from decide_agent.core.decision.chain import ChainResult, ProviderChain
from decide_agent.schemas.error import ErrorSource
from tests.fixtures.providers import FakeProvider, ListSink, score_question


def test_first_available_answers():
    chain = ProviderChain([
        FakeProvider("a", probe=ProbeResult(availability=Availability.UNAVAILABLE, reason="no key")),
        FakeProvider("b"),
    ])
    result = chain.answer(score_question())
    assert result.ok
    assert result.provider == "b"
    assert result.skipped == ["a"]
    assert result.failed == []
    assert result.degraded  # served by a lower tier


def test_first_tier_answers_not_degraded():
    chain = ProviderChain([FakeProvider("a")])
    result = chain.answer(score_question())
    assert result.ok and result.provider == "a"
    assert not result.skipped and not result.failed
    assert not result.degraded


def test_midrun_failure_falls_to_next():
    sink = ListSink()
    chain = ProviderChain(
        [FakeProvider("a", error=RuntimeError("boom")), FakeProvider("b")], sink=sink,
    )
    result = chain.answer(score_question())
    assert result.ok and result.provider == "b"
    assert result.failed == ["a"]
    assert result.errors[0].source is ErrorSource.PROVIDER
    assert "boom" in result.errors[0].detail
    assert result.errors[0].degrade_path == "fall to next provider"
    outcomes = [(r.provider, r.outcome) for r in sink.records]
    assert outcomes == [("a", "failed"), ("b", "ok")]
    assert sink.records[1].degraded_from == ["a"]
    assert sink.records[1].latency_ms is not None


def test_all_exhausted_never_raises():
    sink = ListSink()
    chain = ProviderChain([
        FakeProvider("a", probe=ProbeResult(availability=Availability.UNAVAILABLE)),
        FakeProvider("b", error=ValueError("x")),
    ], sink=sink)
    result = chain.answer(score_question())  # must not raise (L4)
    assert not result.ok
    assert result.answer is None
    assert result.skipped == ["a"] and result.failed == ["b"]
    assert len(result.errors) == 1
    assert [(r.provider, r.outcome) for r in sink.records] == [("a", "skipped"), ("b", "failed")]


def test_degraded_probe_answers_with_degraded_mark():
    chain = ProviderChain([
        FakeProvider("a", probe=ProbeResult(availability=Availability.DEGRADED, reason="stale rules")),
    ])
    result = chain.answer(score_question())
    assert result.ok
    assert result.answer.degraded is True
    assert result.degraded is False  # no tier above was skipped


def test_probe_exception_counts_unavailable():
    sink = ListSink()
    chain = ProviderChain([
        FakeProvider("a", probe=RuntimeError("probe blew up")),
        FakeProvider("b"),
    ], sink=sink)
    result = chain.answer(score_question())
    assert result.ok and result.provider == "b"
    assert result.skipped == ["a"]
    assert "probe blew up" in sink.records[0].detail


def test_chain_result_defaults():
    empty = ChainResult()
    assert not empty.ok and not empty.degraded


def test_midrun_failure_circuit_breaks_provider():
    """首败熔断：同链第二次调用直接 Skip，不再支付失败代价（§5.1 运行中失败自动降）。"""
    breaker = FakeProvider("remote", error=RuntimeError("400 bad request"))
    chain = ProviderChain([breaker, FakeProvider("local")])
    first = chain.answer(score_question())
    assert first.failed == ["remote"] and breaker.calls == 1
    second = chain.answer(score_question())
    assert second.ok and second.provider == "local"
    assert breaker.calls == 1  # 熔断后不再调用
    assert second.skipped == ["remote"]
    assert "disabled after failure" in second.errors[0].detail if second.errors else True


def test_probe_unavailable_also_breaks():
    dead = FakeProvider("dead", probe=ProbeResult(availability=Availability.UNAVAILABLE, reason="no key"))
    chain = ProviderChain([dead, FakeProvider("local")])
    assert chain.answer(score_question()).ok
    dead._probe = ProbeResult()  # 即使之后可用，进程内保持熔断语义
    second = chain.answer(score_question())
    assert second.skipped == ["dead"]  # 仍跳过（配置级缺失是常驻事实）
    assert dead.calls == 0
