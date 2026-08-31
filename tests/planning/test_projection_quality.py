"""typed projection blocking 품질 테스트."""

from __future__ import annotations

from datetime import time

from jeju_trip.planning.quality import (
    preliminary_jeju_coordinate_check,
    validate_opening_period,
    validate_route_sequences,
)


def test_bus_stop_outside_jeju_prefilter_is_rejected() -> None:
    """제주 사전검사 범위 밖의 정류장 좌표는 publication 전에 거부해야 한다."""

    issues = preliminary_jeju_coordinate_check(37.5665, 126.978)
    assert issues[0].reason_code == "COORDINATE_OUTSIDE_JEJU_PREFILTER"


def test_closing_time_before_opening_time_is_rejected() -> None:
    """폐장시각이 개장시각보다 빠른 당일 운영정보를 거부해야 한다."""

    issues = validate_opening_period(time(18), time(9))
    assert issues[0].reason_code == "OPENING_PERIOD_INVALID"


def test_duplicate_route_sequence_is_rejected() -> None:
    """하나의 노선 publication에서 중복 정류장 순번을 거부해야 한다."""

    issues = validate_route_sequences([1, 2, 2])
    assert issues[0].reason_code == "ROUTE_SEQUENCE_DUPLICATED"
