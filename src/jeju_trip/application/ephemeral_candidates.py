"""생성 후보를 정책 TTL 동안 프로세스 메모리에만 보관한다."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from jeju_trip.domain.models import (
    DataSourceMetadata,
    DayTripResponse,
    EvidenceFact,
    Recommendation,
    Strategy,
)
from jeju_trip.infrastructure.tmap_cache import MAX_TTL_SECONDS


class CandidateEvidenceUnavailable(LookupError):
    """적용에 필요한 메모리 후보가 만료되거나 프로세스에서 유실됐다."""

    code = "CANDIDATE_EVIDENCE_UNAVAILABLE"

    def __init__(self, candidate_id: str) -> None:
        super().__init__(self.code)
        self.candidate_id = candidate_id


@dataclass(frozen=True)
class EphemeralCandidatePayload:
    """적용 검증에 필요한 최소 구조화 후보 projection."""

    candidate_id: str
    strategy: Strategy
    recommendation: Recommendation
    evidence_facts: tuple[EvidenceFact, ...]
    data_sources: tuple[DataSourceMetadata, ...]

    @classmethod
    def from_response(
        cls,
        *,
        candidate_id: str,
        response: DayTripResponse,
        strategy: Strategy | str,
    ) -> EphemeralCandidatePayload:
        if response.status != "success":
            raise ValueError("CANDIDATE_RESPONSE_NOT_SUCCESSFUL")
        selected_strategy = Strategy(strategy)
        matches = tuple(
            recommendation
            for recommendation in response.recommendations
            if recommendation.strategy == selected_strategy
        )
        if len(matches) != 1:
            raise ValueError("CANDIDATE_STRATEGY_NOT_UNIQUE")
        if not candidate_id:
            raise ValueError("CANDIDATE_ID_REQUIRED")
        return cls(
            candidate_id=candidate_id,
            strategy=selected_strategy,
            recommendation=matches[0],
            evidence_facts=response.evidence_facts,
            data_sources=response.data_sources,
        )


class EphemeralGenerationCandidateStore:
    """재시작 복구를 주장하지 않는 thread-safe 후보 메모리 저장소."""

    def __init__(
        self,
        ttl_seconds: int = MAX_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
            raise ValueError("CANDIDATE_CACHE_TTL_OUT_OF_RANGE")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[float, EphemeralCandidatePayload]] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def put(self, payload: EphemeralCandidatePayload) -> None:
        with self._lock:
            self._items[payload.candidate_id] = (
                self._clock() + self._ttl_seconds,
                payload,
            )

    def get(self, candidate_id: str) -> EphemeralCandidatePayload | None:
        now = self._clock()
        with self._lock:
            item = self._items.get(candidate_id)
            if item is None:
                return None
            expires_at, payload = item
            if expires_at <= now:
                self._items.pop(candidate_id, None)
                return None
            return payload

    def require(self, candidate_id: str) -> EphemeralCandidatePayload:
        payload = self.get(candidate_id)
        if payload is None:
            raise CandidateEvidenceUnavailable(candidate_id)
        return payload

    def delete(self, candidate_id: str) -> None:
        with self._lock:
            self._items.pop(candidate_id, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
