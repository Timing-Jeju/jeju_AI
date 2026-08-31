"""공식 XLSX mapping manifest의 평일 시간표 bundle 변환 테스트."""

from __future__ import annotations

from datetime import date

import pytest

from jeju_trip.infrastructure.official_timetable_bundle import (
    build_weekday_timetable_bundle,
)
from jeju_trip.infrastructure.timetable_xlsx import (
    OfficialTimetableRawBundle,
    TimetableWorkbookRejected,
)


def _raw_bundle() -> OfficialTimetableRawBundle:
    return OfficialTimetableRawBundle(
        workbook_rows={},
        mapping_manifest={
            "route_stops": [
                {
                    "provider_route_pattern_id": "JEB-route-1",
                    "provider_stop_id": "JEB-stop-1",
                    "route_sequence": 1,
                    "direction_text": "성산 방면",
                    "latitude": 33.45,
                    "longitude": 126.9,
                },
                {
                    "provider_route_pattern_id": "JEB-route-1",
                    "provider_stop_id": "JEB-stop-2",
                    "route_sequence": 2,
                    "direction_text": "성산 방면",
                    "latitude": 33.46,
                    "longitude": 126.91,
                },
            ],
            "trip_rows": [
                {
                    "trip_id": "weekday-201-1",
                    "provider_route_pattern_ids": ["JEB-route-1"],
                    "direction_text": "성산 방면",
                    "timetable_effective_from": "2024-08-01",
                    "timetable_effective_to": "2026-12-31",
                    "major_stop_times": [
                        {
                            "provider_stop_id": "JEB-stop-1",
                            "stop_sequence": 1,
                            "arrival_time": "0900",
                            "departure_time": "0900",
                        },
                        {
                            "provider_stop_id": "JEB-stop-2",
                            "stop_sequence": 2,
                            "arrival_time": "0930",
                            "departure_time": "0930",
                        },
                    ],
                }
            ],
        },
        service_day_manifest={
            "weekday_source_references": [
                "https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule"
            ],
            "holiday_source_references": [],
            "notice_source_references": ["https://bus.jeju.go.kr/notice/list"],
            "notices_reviewed_through": "2026-08-11",
        },
        source_references=("https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule",),
    )


def test_official_weekday_bundle_covers_august_fourteenth_without_holiday_claim() -> None:
    """8월 14일 평일 bundle은 공휴일 calendar 없이 공식 평일 근거와 stop time을 보존해야 한다."""

    bundle = build_weekday_timetable_bundle(_raw_bundle(), date(2026, 8, 14))

    assert {calendar.day_type for calendar in bundle.service_calendars} == {"WEEKDAY"}
    assert bundle.service_exceptions == ()
    assert bundle.trips[0].route_fact_id == "tago.bus-route:JEB-route-1"
    assert [item.stop_id for item in bundle.stop_times] == [
        "tago.bus-stop:JEB-stop-1",
        "tago.bus-stop:JEB-stop-2",
    ]


def test_official_weekday_bundle_requires_notice_review() -> None:
    """시행일 이후 변경 공지 검토 근거가 없으면 평일 시간표 publication을 만들지 않아야 한다."""

    raw = _raw_bundle()
    raw.service_day_manifest.pop("notices_reviewed_through")

    with pytest.raises(TimetableWorkbookRejected, match="TIMETABLE_NOTICE_REVIEW_MISSING"):
        build_weekday_timetable_bundle(raw, date(2026, 8, 14))


def test_official_weekday_bundle_rejects_non_weekday_target() -> None:
    """평일 전용 builder는 토요일 서비스데이를 평일로 가장하지 않아야 한다."""

    with pytest.raises(TimetableWorkbookRejected, match="TIMETABLE_TARGET_NOT_WEEKDAY"):
        build_weekday_timetable_bundle(_raw_bundle(), date(2026, 8, 15))
