"""다일 장소 중복·피로·누적 이동비 정책 테스트."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from jeju_trip.domain.models import (
    ActivityWindow,
    MultiDayPreferences,
    SelectedDayHistory,
    SelectedPlaceHistory,
    Strategy,
)
from jeju_trip.planning.multi_day import (
    daily_load,
    history_load_penalty,
    history_place_policy,
    overnight_rest_below_preference,
    previous_transport_cost_max,
)
from tests.factories import make_recommendation, make_request

KST = timezone(timedelta(hours=9))


def _request_with_history():
    base = make_request()
    recommendation = make_recommendation(Strategy.BALANCED, 1)
    history = SelectedDayHistory(
        trip_date=base.trip_date,
        activity_window=base.activity_window,
        day_start_at=recommendation.day_start_at,
        day_end_at=recommendation.day_end_at,
        selected_places=(
            SelectedPlaceHistory(
                place_id="place-1", role="visit", evidence_fact_ids=("fact-place-open",)
            ),
            SelectedPlaceHistory(
                place_id="meal-1", role="meal", evidence_fact_ids=("fact-place-open",)
            ),
            SelectedPlaceHistory(
                place_id="rest-1", role="rest", evidence_fact_ids=("fact-place-open",)
            ),
        ),
        totals=recommendation.totals,
        evidence_fact_ids=("fact-place-open",),
    )
    return base.model_copy(
        update={
            "trip_date": date(2026, 8, 16),
            "activity_window": ActivityWindow(
                start_at=datetime(2026, 8, 16, 6, tzinfo=KST),
                end_at=datetime(2026, 8, 16, 19, tzinfo=KST),
            ),
            "previous_days": (history,),
            "multi_day": MultiDayPreferences(
                minimum_overnight_rest_minutes=1_200,
                trip_transport_budget_krw=10_000,
            ),
        }
    )


def test_history_roles_exclude_visits_and_soft_avoid_meals_and_rests() -> None:
    """이전 일정 장소는 관광·식사·휴식 역할별 정책 집합으로 분리돼야 한다."""

    policy = history_place_policy(_request_with_history())

    assert policy.excluded_visit_ids == {"place-1"}
    assert policy.avoided_meal_ids == {"meal-1"}
    assert policy.avoided_rest_ids == {"rest-1"}


def test_history_load_penalty_uses_previous_and_projected_loads() -> None:
    """직전 날짜와 현재 후보 부하가 모두 클수록 0보다 큰 피로 패널티가 생겨야 한다."""

    request = _request_with_history()

    assert daily_load(request.previous_days[0]) > 0
    assert (
        history_load_penalty(
            request,
            walking_distance_meters=5_000,
            transfer_minutes=240,
        )
        > 0
    )


def test_previous_transport_cost_and_short_overnight_rest_are_reported() -> None:
    """이전 이동비 최대값과 선호보다 짧은 숙박 간격을 기존 수치로 계산해야 한다."""

    request = _request_with_history()

    assert previous_transport_cost_max(request) == 0
    assert overnight_rest_below_preference(request) is True
