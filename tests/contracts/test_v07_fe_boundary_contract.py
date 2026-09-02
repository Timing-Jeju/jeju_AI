"""FE 연동용 v0.7 하루 경계와 체류시간 공개 계약 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jeju_trip.domain.models import RecommendDayTripsInput
from tests.factories import make_request

KST = timezone(timedelta(hours=9))


def test_day_boundary_defaults_to_accommodation_when_omitted() -> None:
    """하루 경계를 생략하면 시작과 종료 모두 숙소로 확정해야 한다."""

    request = make_request()

    assert request.start_boundary.place_id == request.accommodation.place_id
    assert request.end_boundary.place_id == request.accommodation.place_id


def test_day_boundary_preserves_distinct_terminal_endpoints() -> None:
    """당일 여행의 도착·출발 terminal은 서로 다른 하루 경계로 보존해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["day_boundary"] = {
        "start_place": {"place_id": "tourapi.place:airport", "name": "제주국제공항"},
        "end_place": {"place_id": "tourapi.place:port", "name": "제주항"},
    }

    request = RecommendDayTripsInput.model_validate(payload)

    assert request.start_boundary.place_id == "tourapi.place:airport"
    assert request.end_boundary.place_id == "tourapi.place:port"


def test_place_duration_preferences_preserve_requested_minutes() -> None:
    """FE 체류시간은 장소별 구조화 제약으로 값 손실 없이 보존해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["place_duration_preferences"] = [
        {"place_id": "place-1", "requested_stay_minutes": 90},
        {"place_id": "place-2", "requested_stay_minutes": 30},
    ]

    request = RecommendDayTripsInput.model_validate(payload)

    assert request.requested_stay_minutes("place-1") == 90
    assert request.requested_stay_minutes("place-2") == 30
    assert request.requested_stay_minutes("place-3") is None


def test_place_duration_preferences_reject_duplicate_place_ids() -> None:
    """같은 장소에 모순될 수 있는 체류시간 제약을 두 번 선언하지 못하게 해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["place_duration_preferences"] = [
        {"place_id": "place-1", "requested_stay_minutes": 90},
        {"place_id": "place-1", "requested_stay_minutes": 30},
    ]

    with pytest.raises(ValidationError, match="unique"):
        RecommendDayTripsInput.model_validate(payload)


def test_v06_input_is_rejected_by_current_contract() -> None:
    """감사용 v0.6 입력은 현재 v0.7 runtime 계약으로 암묵 변환하지 않아야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["schema_version"] = "0.6.0"

    with pytest.raises(ValidationError):
        RecommendDayTripsInput.model_validate(payload)


def test_activity_window_keeps_absolute_kst_boundary() -> None:
    """FE 시각은 임의 체크인 보정 없이 KST 절대 활동창으로 유지해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["trip_date"] = datetime(2026, 8, 31, tzinfo=KST).date()
    payload["activity_window"] = {
        "start_at": datetime(2026, 8, 31, 7, tzinfo=KST),
        "end_at": datetime(2026, 8, 31, 19, tzinfo=KST),
    }

    request = RecommendDayTripsInput.model_validate(payload)

    assert request.activity_window.start_at.hour == 7
    assert request.activity_window.end_at.hour == 19
