"""공개 JSON과 분리된 내부 capability 준비 상태."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityReason(StrEnum):
    """readiness가 닫힌 원인을 exact fallback 전에 보존한다."""

    READY = "READY"
    MISSING = "MISSING"
    COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    SOURCE_NOT_APPROVED = "SOURCE_NOT_APPROVED"


@dataclass(frozen=True)
class CapabilityState:
    """capability의 boolean 결과와 실패 원인을 함께 전달한다."""

    ready: bool
    reason: CapabilityReason

    @classmethod
    def available(cls) -> CapabilityState:
        return cls(True, CapabilityReason.READY)

    @classmethod
    def unavailable(cls, reason: CapabilityReason) -> CapabilityState:
        if reason == CapabilityReason.READY:
            raise ValueError("UNAVAILABLE_CAPABILITY_REQUIRES_FAILURE_REASON")
        return cls(False, reason)
