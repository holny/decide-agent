"""DecisionError contract tests: defaults, disclosure line, JSON roundtrip."""
from decide_agent.schemas.error import DecisionError, ErrorSeverity, ErrorSource


def test_defaults_are_degradable():
    err = DecisionError(code="provider.x.failed")
    assert err.severity is ErrorSeverity.DEGRADABLE
    assert err.source is ErrorSource.PROVIDER


def test_disclose_with_and_without_path():
    plain = DecisionError(code="provider.unavailable", detail="no key")
    assert plain.disclose() == "[provider.unavailable] no key"
    pathed = DecisionError(
        code="tool.deals.failed", source=ErrorSource.TOOL,
        detail="timeout", degrade_path="skip + caveat",
    )
    assert pathed.disclose() == "[tool.deals.failed] timeout（skip + caveat）"


def test_json_roundtrip():
    err = DecisionError(code="decision.expired", source=ErrorSource.EXPIRED, severity=ErrorSeverity.FATAL)
    restored = DecisionError.model_validate_json(err.model_dump_json())
    assert restored == err
