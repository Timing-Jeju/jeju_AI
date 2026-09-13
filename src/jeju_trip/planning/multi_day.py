"""이전 선택 일정에서 중복·피로·누적 이동비 정책을 결정론적으로 계산한다."""

from __future__ import annotations

from dataclasses import dataclass

from jeju_trip.domain.models import RecommendDayTripsInput, SelectedDayHistory


@dataclass(frozen=True)
class HistoryPlacePolicy:
    excluded_visit_ids: frozenset[str]
    avoided_meal_ids: frozenset[str]
    avoided_rest_ids: frozenset[str]


def history_place_policy(request: RecommendDayTripsInput) -> HistoryPlacePolicy:
    """이전 최소 이력의 역할별 장소 ID를 공개 중복 정책으로 분류한다."""

    by_type: dict[str, set[str]] = {"visit": set(), "meal": set(), "rest": set()}
    for day in request.previous_days:
        for place in day.selected_places:
            by_type[place.role].add(place.place_id)
    return HistoryPlacePolicy(
        excluded_visit_ids=frozenset(by_type["visit"]),
        avoided_meal_ids=frozenset(by_type["meal"]),
        avoided_rest_ids=frozenset(by_type["rest"]),
    )


def daily_load(day: SelectedDayHistory) -> float:
    """기존 수치만으로 날짜별 도보·이동 부하를 계산한다."""

    available = max(
        1,
        int(
            (
                day.activity_window.end_at
                - day.activity_window.start_at
            ).total_seconds()
            // 60
        ),
    )
    walking_load = day.totals.walking_minutes / available
    transfer_load = day.totals.transfer_minutes / available
    return 0.5 * min(walking_load, 2.0) + 0.5 * min(transfer_load, 2.0)


def history_load_penalty(
    request: RecommendDayTripsInput,
    *,
    walking_distance_meters: int,
    transfer_minutes: int,
) -> float:
    """직전 날짜 부하와 현재 예상 부하의 곱을 0~100 패널티로 바꾼다."""

    if not request.previous_days:
        return 0.0
    available = max(
        1,
        int(
            (request.activity_window.end_at - request.activity_window.start_at).total_seconds()
            // 60
        ),
    )
    maximum_walk = max(1, request.walking.max_total_distance_meters)
    projected = 0.5 * min(walking_distance_meters / maximum_walk, 2.0) + 0.5 * min(
        transfer_minutes / available, 2.0
    )
    return 100 * min(1.0, daily_load(request.previous_days[-1]) * projected)


def previous_transport_cost_max(request: RecommendDayTripsInput) -> int:
    """이전 선택 일정의 이동비 최대값만 누적한다."""

    return sum(
        day.totals.estimated_cost.max_krw for day in request.previous_days
    )


def overnight_rest_below_preference(request: RecommendDayTripsInput) -> bool:
    """직전 숙소 복귀부터 현재 출발까지 선호 휴식보다 짧은지 판정한다."""

    if not request.previous_days:
        return False
    previous_return = request.previous_days[-1].day_end_at
    minutes = int((request.activity_window.start_at - previous_return).total_seconds() // 60)
    return minutes < request.multi_day.minimum_overnight_rest_minutes
