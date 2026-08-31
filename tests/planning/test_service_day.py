"""버스 서비스데이 판정 테스트."""

from __future__ import annotations

from datetime import date

import pytest

from jeju_trip.planning.service_day import resolve_service_day


@pytest.mark.parametrize(
    ("trip_date", "holidays", "expected"),
    [
        pytest.param(date(2026, 7, 20), set(), "WEEKDAY", id="평일 시간표"),
        pytest.param(date(2026, 7, 25), set(), "SATURDAY", id="토요일 시간표"),
        pytest.param(date(2026, 8, 15), {date(2026, 8, 15)}, "HOLIDAY", id="공휴일 시간표"),
    ],
)
def test_service_calendar_selection(trip_date, holidays, expected) -> None:
    """여행 날짜의 서비스데이에 맞는 버스 시간표를 선택해야 한다."""

    assert resolve_service_day(trip_date, holidays) == expected
