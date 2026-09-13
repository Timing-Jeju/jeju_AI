"""AI 후보의 프로세스 메모리 전용 보관 정책 테스트."""

from __future__ import annotations

from dataclasses import fields

import pytest

from jeju_trip.application.ephemeral_candidates import (
    CandidateEvidenceUnavailable,
    EphemeralCandidatePayload,
    EphemeralGenerationCandidateStore,
)
from jeju_trip.infrastructure.tmap_cache import MAX_TTL_SECONDS
from tests.factories import make_success_response


def test_candidate_store_rejects_ttl_beyond_tmap_policy() -> None:
    """후보 저장소는 TMAP 정책의 23시간 50분을 넘는 TTL을 허용하지 않아야 한다."""

    with pytest.raises(ValueError, match="CANDIDATE_CACHE_TTL_OUT_OF_RANGE"):
        EphemeralGenerationCandidateStore(ttl_seconds=MAX_TTL_SECONDS + 1)


def test_candidate_store_expires_at_policy_boundary() -> None:
    """프로세스 재시작 또는 TTL 만료로 후보가 없으면 재생성 가능한 구조화 오류를 내야 한다."""

    now = 100.0
    store = EphemeralGenerationCandidateStore(ttl_seconds=10, clock=lambda: now)
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
