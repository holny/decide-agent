"""Availability verdicts: providers self-report probes, the chain only judges.

Core never does network I/O — probing transports live inside each provider
implementation (adapter layer). This module defines the verdict vocabulary and
pure judges over probe outcomes.
"""
import os
from enum import Enum

from pydantic import BaseModel, Field


class Availability(str, Enum):
    AVAILABLE = "available"  # chain will attempt to answer
    DEGRADED = "degraded"  # chain will attempt; quality/coverage reduced
    UNAVAILABLE = "unavailable"  # skipped without attempt (missing key/config/deps)


_RANK = {Availability.UNAVAILABLE: 0, Availability.DEGRADED: 1, Availability.AVAILABLE: 2}


class ProbeResult(BaseModel):
    availability: Availability = Availability.AVAILABLE
    reason: str | None = Field(default=None, description="why degraded/unavailable, for disclosure")


def key_probe(key_env: str | None) -> ProbeResult:
    """Pure, network-free credential probe: env var present?

    key_env=None means the provider needs no credential (e.g. self-hosted Laya).
    """
    if key_env is None or os.environ.get(key_env):
        return ProbeResult()
    return ProbeResult(
        availability=Availability.UNAVAILABLE,
        reason=f"env {key_env} not set",
    )


def worst(*results: ProbeResult) -> ProbeResult:
    """Combine probes: any UNAVAILABLE wins, then any DEGRADED, else AVAILABLE."""
    if not results:
        return ProbeResult()
    combined = min(results, key=lambda r: _RANK[r.availability])
    return combined.model_copy()
