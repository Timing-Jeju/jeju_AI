"""v0.7 다일 입력과 분리된 안전도 상태 계약을 검증한다."""

from __future__ import annotations

import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from jeju_trip.domain.models import (
    CommonTripInput,
    ProgressInput,
    RecommendDayTripsInput,
    SelectedDayHistory,
    Strategy,
)
from tests.factories import make_recommendation, make_request

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[2]


def _history() -> SelectedDayHistory:
    request = make_request()
    return SelectedDayHistory(
        day_conditions=CommonTripInput.model_validate(
            request.model_dump(
                mode="python",
                exclude={
                    "request_mode",
                    "required_places",
                    "preferred_places",
                    "excluded_places",
                    "discovery",
                    "original_text",
                    "previous_days",
                    "multi_day",
                },
            )
        ),
        selected_recommendation=make_recommendation(Strategy.BALANCED, 1),
    )


def _next_day_payload() -> dict[str, object]:
    request = make_request()
    return {
        **request.model_dump(mode="python"),
        "trip_date": date(2026, 8, 16),
        "activity_window": {
            "start_at": datetime(2026, 8, 16, 9, tzinfo=KST),
            "end_at": datetime(2026, 8, 16, 20, tzinfo=KST),
        },
        "previous_days": [_history().model_dump(mode="python")],
    }


def test_v06_input_is_rejected_without_implicit_migration() -> None:
    """v0.6 입력은 v0.7 공개 계약으로 암묵 변환되지 않아야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["schema_version"] = "0.6.0"
    with pytest.raises(ValidationError):
        RecommendDayTripsInput.model_validate(payload)


def test_package_version_matches_public_v07_contract() -> None:
    """배포 패키지 버전은 현재 공개 JSON 계약 버전과 정확히 일치해야 한다."""

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["version"] == "0.7.0"


def test_previous_days_must_be_unique_and_ascending() -> None:
    """이전 날짜는 중복 없이 오름차순이어야 한다."""

    payload = _next_day_payload()
    payload["previous_days"] = [
        _history().model_dump(mode="python"),
        _history().model_dump(mode="python"),
    ]
    with pytest.raises(ValidationError, match="unique"):
        RecommendDayTripsInput.model_validate(payload)


def test_required_previous_visit_is_a_hard_conflict() -> None:
    """이전 관광지를 현재 필수 장소로 다시 요청하면 전체 입력을 거부해야 한다."""

    payload = _next_day_payload()
    payload["required_places"] = [{"place_id": "place-1"}]
    with pytest.raises(ValidationError, match="PLACE_ALREADY_VISITED_CONFLICT"):
        RecommendDayTripsInput.model_validate(payload)


def test_required_previous_visit_uses_nested_visit_identity() -> None:
    """이전 관광 이벤트의 공통 장소 ID가 비어도 상세 방문 ID로 중복을 차단해야 한다."""

    payload = _next_day_payload()
    previous_days = payload["previous_days"]
    assert isinstance(previous_days, list)
    previous_days[0]["selected_recommendation"]["timeline"][0]["place_id"] = None
    payload["required_places"] = [{"place_id": "place-1"}]

    with pytest.raises(ValidationError, match="PLACE_ALREADY_VISITED_CONFLICT"):
        RecommendDayTripsInput.model_validate(payload)


def test_at_place_requires_actual_activity_start() -> None:
    """장소 체류 상태는 실제 활동 시작시각 없이는 재검증할 수 없어야 한다."""

    checked_at = datetime(2026, 8, 15, 10, tzinfo=KST)
    with pytest.raises(ValidationError, match="current_event_started_at"):
        ProgressInput(
            state="at_place",
            current_event_id="visit-1",
            actual_time=checked_at,
            current_place_id="place-1",
        )


def test_actual_activity_start_cannot_follow_check_time() -> None:
    """실제 활동 시작시각은 확인시각보다 미래일 수 없어야 한다."""

    checked_at = datetime(2026, 8, 15, 10, tzinfo=KST)
    with pytest.raises(ValidationError, match="must not follow"):
        ProgressInput(
            state="at_place",
            current_event_id="visit-1",
            actual_time=checked_at,
            current_place_id="place-1",
            current_event_started_at=checked_at + timedelta(minutes=1),
        )
