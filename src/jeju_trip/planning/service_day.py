"""공휴일과 요일을 서비스데이로 결정하되 시간표 선택과 분리한다."""

from __future__ import annotations

from datetime import date
from typing import Literal

ServiceDay = Literal["WEEKDAY", "SATURDAY", "SUNDAY", "HOLIDAY"]


def resolve_service_day(trip_date: date, official_holidays: set[date]) -> ServiceDay:
    if trip_date in official_holidays:
        return "HOLIDAY"
    if trip_date.weekday() == 5:
        return "SATURDAY"
    if trip_date.weekday() == 6:
        return "SUNDAY"
    return "WEEKDAY"
