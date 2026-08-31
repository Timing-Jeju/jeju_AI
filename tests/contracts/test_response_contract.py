"""v0.5 공개 응답 불변조건 테스트."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from jeju_trip.domain.models import (
    CostRange,
    DayTripResponse,
    Derivation,
    EvaluationResponse,
    EvidenceFact,
    NormalizedSchedule,
    PreviewTransferResponse,
    RecoveryOption,
    RevalidationResponse,
    SegmentEvaluation,
    SourceRef,
    ValidationSummary,
)
from tests.factories import make_success_response


def test_success_response_requires_three_recommendations() -> None:
    """성공 응답에는 서로 다른 추천 경로가 정확히 세 개 포함되어야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["recommendations"] = payload["recommendations"][:2]
    with pytest.raises(ValidationError, match="exactly three"):
        DayTripResponse.model_validate(payload)


def test_success_response_requires_each_strategy() -> None:
    """성공 응답은 세 추천 전략을 각각 한 번씩 사용해야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["recommendations"][2]["strategy"] = "balanced"
    with pytest.raises(ValidationError, match="one recommendation"):
        DayTripResponse.model_validate(payload)


def test_success_response_rejects_effectively_identical_routes() -> None:
    """세 전략의 장소와 이동 방식이 모두 같으면 성공으로 반환하지 않아야 한다."""

    payload = make_success_response().model_dump(mode="json")
    for recommendation in payload["recommendations"]:
        recommendation["place_ids"] = ["same-place"]
    with pytest.raises(ValidationError, match="must be diverse"):
        DayTripResponse.model_validate(payload)


def test_model_cannot_introduce_unknown_evidence_fact() -> None:
    """모델이 존재하지 않는 근거 ID를 사용한 추천 이유는 반환되지 않아야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["recommendations"][0]["recommendation_reasons"][0]["evidence_fact_ids"] = [
        "unknown-fact"
    ]
    with pytest.raises(ValidationError, match="unknown evidence"):
        DayTripResponse.model_validate(payload)


def test_generation_source_refs_must_exist_in_data_sources() -> None:
    """추천 evidence의 source ID는 같은 응답 data_sources ledger에 반드시 존재해야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["data_sources"] = []

    with pytest.raises(ValidationError, match="unknown data source"):
        DayTripResponse.model_validate(payload)


def test_data_source_ledger_rejects_duplicate_source_ids() -> None:
    """응답 source ledger는 동일 source ID의 상충 metadata를 중복 허용하지 않아야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["data_sources"] = [payload["data_sources"][0], payload["data_sources"][0]]

    with pytest.raises(ValidationError, match="duplicate source IDs"):
        DayTripResponse.model_validate(payload)


def test_totals_cannot_reference_unknown_evidence_fact() -> None:
    """계산 totals도 응답 ledger에 없는 근거 ID를 참조할 수 없어야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["recommendations"][0]["totals"]["derivation_evidence_fact_ids"] = [
        "unknown-total-fact"
    ]

    with pytest.raises(ValidationError, match="unknown evidence"):
        DayTripResponse.model_validate(payload)


def test_place_decision_cannot_reference_unknown_evidence_fact() -> None:
    """요청 장소 포함·제외 판단도 응답 ledger에 없는 근거 ID를 참조할 수 없어야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["place_decisions"] = [
        {
            "place_id": "place-1",
            "requested_priority": "preferred",
            "decision": "included",
            "evidence_fact_ids": ["unknown-place-decision-fact"],
        }
    ]

    with pytest.raises(ValidationError, match="unknown evidence"):
        DayTripResponse.model_validate(payload)


def _minimal_evaluation() -> EvaluationResponse:
    return EvaluationResponse(
        status="feasible",
        timing_status="on_schedule",
        evidence_status="verified",
        overall_risk="low",
        schedule_window_fit=True,
        normalized_schedule=NormalizedSchedule(events=()),
        validation=ValidationSummary(
            schema_valid=True,
            timeline_valid=True,
            provenance_valid=True,
            transit_connections_valid=True,
            diversity_valid=True,
        ),
    )


def _source_evidence() -> EvidenceFact:
    return EvidenceFact(
        fact_id="fact-source",
        category="route",
        value=True,
        source_refs=(SourceRef(source_id="tmap.pedestrian"),),
        data_as_of=date(2026, 8, 15),
        retrieved_at=datetime.fromisoformat("2026-08-15T09:00:00+09:00"),
        confidence=1,
        derivation=Derivation(kind="source"),
    )


def test_evaluation_source_refs_must_exist_in_data_sources() -> None:
    """판정 evidence의 source ID도 응답 data_sources 밖을 참조할 수 없어야 한다."""

    payload = _minimal_evaluation().model_dump(mode="json")
    payload["evidence_facts"] = [_source_evidence().model_dump(mode="json")]

    with pytest.raises(ValidationError, match="unknown data source"):
        EvaluationResponse.model_validate(payload)


def test_transfer_preview_source_refs_must_exist_in_data_sources() -> None:
    """이동 미리보기 evidence도 source metadata와 함께 닫힌 계약을 반환해야 한다."""

    with pytest.raises(ValidationError, match="unknown data source"):
        PreviewTransferResponse(
            status="success",
            evidence_facts=(_source_evidence(),),
        )


def test_evaluation_cannot_reference_unknown_evidence_fact() -> None:
    """판정 segment는 응답 ledger에 없는 이동 근거 ID를 참조할 수 없어야 한다."""

    evaluation = _minimal_evaluation()
    payload = evaluation.model_dump(mode="json")
    payload["segment_evaluations"] = [
        SegmentEvaluation(
            segment_id="segment-1",
            from_event_id=None,
            to_event_id=None,
            mode="walk",
            status="feasible",
            risk="low",
            available_minutes=30,
            required_minutes=10,
            slack_minutes=20,
            evidence_fact_ids=("unknown-segment-fact",),
        ).model_dump(mode="json")
    ]

    with pytest.raises(ValidationError, match="unknown evidence"):
        EvaluationResponse.model_validate(payload)


def test_revalidation_recovery_cannot_reference_unknown_evidence_fact() -> None:
    """실시간 회복안도 포함된 판정 ledger에 없는 근거 ID를 참조할 수 없어야 한다."""

    evaluation = _minimal_evaluation()
    payload = RevalidationResponse(
        status="on_schedule",
        timing_status="on_schedule",
        evidence_status="verified",
        checked_at=datetime.fromisoformat("2026-08-15T10:00:00+09:00"),
        delay_minutes=0,
        location_basis="event",
        original_evaluation=evaluation,
    ).model_dump(mode="json")
    payload["recovery_options"] = [
        RecoveryOption(
            recovery_id="recovery-1",
            action="WAIT_BUS",
            affected_event_ids=("segment-1",),
            result_status="on_schedule",
            evidence_fact_ids=("unknown-recovery-fact",),
            expected_cost=CostRange(min_krw=0, max_krw=0, is_estimated=False),
        ).model_dump(mode="json")
    ]

    with pytest.raises(ValidationError, match="unknown evidence"):
        RevalidationResponse.model_validate(payload)


def test_timeline_totals_must_match_events() -> None:
    """타임라인의 이벤트 합계와 추천 totals가 정확히 일치해야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["recommendations"][0]["totals"]["total_minutes"] = 61
    with pytest.raises(ValidationError, match="timeline total"):
        DayTripResponse.model_validate(payload)


def test_success_requires_every_required_place_in_all_routes() -> None:
    """필수 장소는 성공한 세 추천 모두에 포함되어야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["request"]["required_places"] = [{"place_id": "must-visit", "name": "필수 장소"}]
    with pytest.raises(ValidationError, match="every required place"):
        DayTripResponse.model_validate(payload)


def test_visit_detail_times_must_match_timeline() -> None:
    """장소별 도착·입장·출발·체류 시각은 해당 방문 이벤트와 모순될 수 없어야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["recommendations"][0]["timeline"][0]["visit"]["departure_at"] = (
        "2026-08-15T20:00:00+09:00"
    )
    with pytest.raises(ValidationError, match="visit timing details"):
        DayTripResponse.model_validate(payload)


def test_accommodation_boundary_times_must_match_timeline() -> None:
    """숙소 출발·복귀 시각은 첫 이벤트 시작과 마지막 이벤트 종료에 정확히 맞아야 한다."""

    payload = make_success_response().model_dump(mode="json")
    payload["recommendations"][0]["accommodation_return_at"] = "2026-08-15T20:00:00+09:00"
    with pytest.raises(ValidationError, match="accommodation return"):
        DayTripResponse.model_validate(payload)
