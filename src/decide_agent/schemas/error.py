"""DecisionError: unified error contract (L4: any gap degrades, never hard-fails).

Errors are data, not exceptions: providers/tools/timeout/expiry all surface as
DecisionError instances so degrade paths and missing_information disclosure stay
structured end-to-end.
"""
from enum import Enum

from pydantic import BaseModel, Field


class ErrorSeverity(str, Enum):
    DEGRADABLE = "degradable"  # chain/flow continues on a fallback path
    FATAL = "fatal"  # decision cannot proceed (block_to_clarify / invalid input)


class ErrorSource(str, Enum):
    PROVIDER = "provider"  # judgment provider unavailable/failed
    TOOL = "tool"  # collect tool failed
    TIMEOUT = "timeout"  # clock budget exceeded
    EXPIRED = "expired"  # decision / pending request no longer recoverable
    CONFIG = "config"  # invalid configuration
    INPUT = "input"  # invalid input (schema mismatch, empty candidates...)


class DecisionError(BaseModel):
    code: str = Field(
        description="machine-readable dotted code, e.g. provider.unavailable / decision.expired",
    )
    severity: ErrorSeverity = ErrorSeverity.DEGRADABLE
    source: ErrorSource = ErrorSource.PROVIDER
    detail: str | None = Field(default=None, description="human-readable context")
    degrade_path: str | None = Field(
        default=None,
        description="fallback taken (or offered), surfaced via missing_information",
    )

    def disclose(self) -> str:
        """One-line missing_information entry."""
        base = f"[{self.code}] {self.detail}".rstrip()
        return f"{base}（{self.degrade_path}）" if self.degrade_path else base
