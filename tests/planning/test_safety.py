"""도보와 승차 안전시간 테스트."""

from __future__ import annotations

from datetime import datetime

from jeju_trip.planning.safety import calculate_boarding_safety, calculate_planned_walk
from tests.factories import KST


def test_expected_and_planned_walk_times_are_separate() -> None:
    """도보 API 예상시간과 정책을 반영한 계획시간을 분리해야 한다."""

    result = calculate_planned_walk(8, 1.15, 3)
    assert result.expected_minutes == 8
    assert result.planned_minutes == 13


def test_boarding_safety_calculates_latest_origin_departure() -> None:
    """승차 버퍼와 준비시간을 반영해 가장 늦은 출발시각을 계산해야 한다."""

    result = calculate_boarding_safety(
        datetime(2026, 8, 15, 10, tzinfo=KST),
        planned_access_walk_minutes=13,
        boarding_buffer_minutes=7,
        departure_preparation_minutes=3,
    )
    assert result.recommended_stop_arrival_at.minute == 53
    assert result.latest_safe_origin_departure_at.minute == 37
