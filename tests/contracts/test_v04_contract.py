"""v0.5 생성·정확 일정·실시간 공개 계약 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from jeju_trip.domain.models import (
    EvaluateJejuDayTripInput,
    RecommendDayTripsInput,
    RevalidateJejuDayTripInput,
)

KST = timezone(timedelta(hours=9))


def _common_payload() -> dict[str, object]:
    return {
        "schema_version": "0.7.0",
        "trip_date": "2026-08-15",
        "timezone": "Asia/Seoul",
        "accommodation": {
            "name": "제주 숙소",
            "coordinates": {"latitude": 33.489, "longitude": 126.498},
        },
        "activity_window": {
            "start_at": "2026-08-15T09:00:00+09:00",
            "end_at": "2026-08-15T21:00:00+09:00",
        },
    }


def test_recommend_contract_rejects_non_kst_offset() -> None:
    """생성 입력의 모든 시각은 서울 표준시 오프셋이어야 한다."""

    payload = _common_payload()
    payload["activity_window"] = {
        "start_at": "2026-08-15T09:00:00+00:00",
        "end_at": "2026-08-15T21:00:00+00:00",
    }
    with pytest.raises(ValidationError, match=r"\+09:00"):
        RecommendDayTripsInput.model_validate(payload)


def test_exact_schedule_union_rejects_mixed_formats() -> None:
    """활동 전용 일정과 전체 타임라인 필드는 한 요청에서 섞일 수 없다."""

    payload = {
        **_common_payload(),
        "schedule_format": "activities_only",
        "scheduled_activities": [],
        "timeline": [],
    }
    with pytest.raises(ValidationError):
        TypeAdapter(EvaluateJejuDayTripInput).validate_python(payload)


def test_revalidation_requires_checked_at_kst() -> None:
    """실시간 확인 시각도 서울 표준시 오프셋을 포함해야 한다."""

    with pytest.raises(ValidationError, match=r"\+09:00"):
        RevalidateJejuDayTripInput.model_validate(
            {
                "schema_version": "0.7.0",
                "checked_at": "2026-08-15T12:20:00+00:00",
                "progress": {
                    "state": "ready_to_depart",
                    "current_event_id": "visit-1",
                    "actual_time": "2026-08-15T12:20:00+09:00",
                    "completed_event_ids": [],
                    "current_place_id": "place-1",
                },
                "itinerary": {
                    **_common_payload(),
                    "schedule_format": "activities_only",
                    "scheduled_activities": [],
                },
            }
        )


def test_activity_window_cannot_exceed_twenty_four_hours() -> None:
    """하루 활동 시간은 최대 스물네 시간을 넘을 수 없다."""

    payload = _common_payload()
    payload["activity_window"] = {
        "start_at": datetime(2026, 8, 15, 9, tzinfo=KST),
        "end_at": datetime(2026, 8, 16, 10, tzinfo=KST),
    }
    with pytest.raises(ValidationError, match="24 hours"):
        RecommendDayTripsInput.model_validate(payload)


@pytest.mark.parametrize("mode", ["bus", "taxi", "walk"], ids=["버스", "택시", "도보"])
def test_single_allowed_transport_mode_gets_safe_defaults(mode: str) -> None:
    """단일 허용수단 요청은 다른 수단의 기본값 때문에 거부되지 않아야 한다."""

    payload = _common_payload()
    payload["transport"] = {"allowed_modes": [mode]}
    request = RecommendDayTripsInput.model_validate(payload)
    assert request.transport.preferred_mode == mode
    assert request.transport.fallback_order == ()
