"""다음 Day로 전달하는 최소 선택 이력 공개 계약 테스트."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jeju_trip.domain.models import RecommendDayTripsInput, Strategy
from tests.factories import make_recommendation, make_request

KST = timezone(timedelta(hours=9))


def _minimal_history_payload() -> dict[str, object]:
    recommendation = make_recommendation(Strategy.BALANCED, 1)
    return {
        "trip_date": date(2026, 8, 15),
        "activity_window": {
            "start_at": datetime(2026, 8, 15, 9, tzinfo=KST),
            "end_at": datetime(2026, 8, 15, 20, tzinfo=KST),
        },
        "day_start_at": recommendation.day_start_at,
        "day_end_at": recommendation.day_end_at,
        "selected_places": [
            {
                "place_id": "place-1",
                "role": "visit",
                "evidence_fact_ids": ["fact-place-open"],
            },
            {
                "place_id": "meal-1",
                "role": "meal",
                "evidence_fact_ids": ["fact-place-open"],
            },
            {
                "place_id": "rest-1",
                "role": "rest",
                "evidence_fact_ids": ["fact-place-open"],
            },
        ],
        "totals": recommendation.totals.model_dump(mode="python"),
        "evidence_fact_ids": ["fact-place-open"],
    }


def _next_day_payload() -> dict[str, object]:
    payload = make_request().model_dump(mode="python")
    payload["trip_date"] = date(2026, 8, 16)
    payload["activity_window"] = {
        "start_at": datetime(2026, 8, 16, 9, tzinfo=KST),
        "end_at": datetime(2026, 8, 16, 20, tzinfo=KST),
    }
    payload["previous_days"] = [_minimal_history_payload()]
    return payload


def test_previous_day_accepts_only_minimal_selected_history() -> None:
    """다음 Day 입력은 이전 전체 일정 대신 장소·합계·fact ID 최소 이력만 받아야 한다."""

    request = RecommendDayTripsInput.model_validate(_next_day_payload())

    history = request.previous_days[0]
    assert history.trip_date == date(2026, 8, 15)
    assert {place.place_id for place in history.selected_places} == {
        "place-1",
        "meal-1",
        "rest-1",
    }
    assert history.evidence_fact_ids == ("fact-place-open",)
    assert not hasattr(history, "selected_recommendation")


def test_previous_day_rejects_unknown_place_fact_reference() -> None:
    """이전 장소 이력은 보존된 Day fact ID 집합 밖의 근거를 참조하지 못해야 한다."""

    payload = _next_day_payload()
    previous_days = payload["previous_days"]
    assert isinstance(previous_days, list)
    previous_days[0]["selected_places"][0]["evidence_fact_ids"] = ["fact-invented"]

    with pytest.raises(ValidationError, match="unknown evidence fact"):
        RecommendDayTripsInput.model_validate(payload)


def test_previous_day_rejects_full_recommendation_payload() -> None:
    """이전 Day 전체 타임라인과 설명을 다시 전달하는 과거 계약은 거부해야 한다."""

    payload = _next_day_payload()
    previous_days = payload["previous_days"]
    assert isinstance(previous_days, list)
    previous_days[0]["selected_recommendation"] = make_recommendation(
        Strategy.BALANCED, 1
    ).model_dump(mode="python")

    with pytest.raises(ValidationError, match="selected_recommendation"):
        RecommendDayTripsInput.model_validate(payload)
