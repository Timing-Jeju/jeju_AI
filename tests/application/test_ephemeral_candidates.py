"""AI 후보의 프로세스 메모리 전용 보관 정책 테스트."""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta
from threading import Event

import pytest

from jeju_trip.application.ephemeral_candidates import (
    CandidateEvidenceUnavailable,
    EphemeralCandidatePayload,
    EphemeralGenerationCandidateStore,
)
from jeju_trip.domain.models import SourceRef
from jeju_trip.infrastructure.tmap_cache import MAX_TTL_SECONDS
from tests.factories import make_success_response


def test_candidate_store_rejects_ttl_beyond_tmap_policy() -> None:
    """후보 저장소는 TMAP 정책의 23시간 50분을 넘는 TTL을 허용하지 않아야 한다."""

    with pytest.raises(ValueError, match="CANDIDATE_CACHE_TTL_OUT_OF_RANGE"):
        EphemeralGenerationCandidateStore(ttl_seconds=MAX_TTL_SECONDS + 1)


def test_candidate_store_expires_at_policy_boundary() -> None:
    """프로세스 재시작 또는 TTL 만료로 후보가 없으면 재생성 가능한 구조화 오류를 내야 한다."""

    now = 100.0
    store = EphemeralGenerationCandidateStore(
        ttl_seconds=10, clock=lambda: now,
        wall_clock=lambda: make_success_response().generated_at,
    )
    payload = EphemeralCandidatePayload.from_response(
        candidate_id="candidate-balanced",
        response=make_success_response(),
        strategy="balanced",
    )
    store.put(payload)

    now = 110.0

    with pytest.raises(CandidateEvidenceUnavailable) as raised:
        store.require("candidate-balanced")
    assert raised.value.code == "CANDIDATE_EVIDENCE_UNAVAILABLE"


def test_candidate_payload_keeps_only_apply_evidence_projection() -> None:
    """메모리 후보에는 요청 원문·TMAP 원본·상세 geometry 필드를 복사하지 않아야 한다."""

    payload = EphemeralCandidatePayload.from_response(
        candidate_id="candidate-relaxed",
        response=make_success_response(),
        strategy="relaxed",
    )

    stored_fields = {field.name for field in fields(payload)}
    assert stored_fields == {
        "candidate_id",
        "strategy",
        "recommendation",
        "evidence_facts",
        "data_sources",
        "expires_at",
    }
    assert payload.recommendation.strategy == "relaxed"
    assert "original_text" not in payload.__dict__
    assert "raw_response" not in payload.__dict__
    assert "geometry" not in payload.__dict__


def test_candidate_payload_rejects_failure_or_missing_strategy() -> None:
    """부분 결과나 요청한 전략이 없는 응답은 적용 후보로 저장하지 않아야 한다."""

    response = make_success_response()
    failed = response.model_copy(
        update={"status": "insufficient_feasible_routes", "recommendations": ()}
    )

    with pytest.raises(ValueError, match="CANDIDATE_RESPONSE_NOT_SUCCESSFUL"):
        EphemeralCandidatePayload.from_response(
            candidate_id="candidate-failed",
            response=failed,
            strategy="balanced",
        )


def test_reinserting_candidate_does_not_extend_original_deadline() -> None:
    """같은 후보를 재저장해도 최초 메모리 보존 만료시각이 연장되지 않아야 한다."""
    now = 0.0
    response = make_success_response()
    store = EphemeralGenerationCandidateStore(
        ttl_seconds=10, clock=lambda: now, wall_clock=lambda: response.generated_at
    )
    payload = EphemeralCandidatePayload.from_response(
        candidate_id="repeat", response=response, strategy="balanced"
    )
    store.put(payload)
    now = 9.0
    store.put(payload)
    now = 10.0
    assert store.get("repeat") is None


def test_expired_response_cannot_receive_fresh_cache_ttl() -> None:
    """이미 만료된 생성 결과를 새 저장소에 넣어도 근거 보존기간이 갱신되지 않아야 한다."""
    response = make_success_response()
    store = EphemeralGenerationCandidateStore(
        wall_clock=lambda: response.generated_at + timedelta(days=1)
    )
    payload = EphemeralCandidatePayload.from_response(
        candidate_id="expired", response=response, strategy="balanced"
    )
    with pytest.raises(CandidateEvidenceUnavailable):
        store.put(payload)


def test_old_tmap_fact_limits_candidate_deadline() -> None:
    """캐시에서 재사용한 TMAP 근거의 취득시각이 후보 만료 상한을 결정해야 한다."""
    response = make_success_response()
    fact = response.evidence_facts[0].model_copy(update={
        "source_refs": (SourceRef(source_id="tmap.driving"),),
        "retrieved_at": response.generated_at - timedelta(hours=23),
    })
    response = response.model_copy(update={
        "evidence_facts": (fact,),
        "data_sources": (response.data_sources[0].model_copy(
            update={"source_id": "tmap.driving"}
        ),),
    })
    payload = EphemeralCandidatePayload.from_response(
        candidate_id="old-fact", response=response, strategy="balanced"
    )
    assert payload.expires_at == response.generated_at + timedelta(minutes=50)


def test_idle_candidate_is_removed_without_read_request() -> None:
    """만료 후보는 조회 요청이 없어도 저장소와 타이머에서 제거돼야 한다."""
    removed = Event()

    class ObservedStore(EphemeralGenerationCandidateStore):
        def _expire(self, candidate_id: str, deadline: float) -> None:
            super()._expire(candidate_id, deadline)
            removed.set()

    response = make_success_response()
    store = ObservedStore(ttl_seconds=1, wall_clock=lambda: response.generated_at)
    store.put(EphemeralCandidatePayload.from_response(
        candidate_id="idle", response=response, strategy="balanced"
    ))
    try:
        assert removed.wait(timeout=3)
        assert not store._items
        assert not store._timers
    finally:
        store.clear()
