"""공식 XLSX raw ZIP manifest를 평일 typed timetable bundle로 변환한다."""

from __future__ import annotations

from datetime import date
from typing import Any

from jeju_trip.infrastructure.projection_publisher import TimetableBundle
from jeju_trip.infrastructure.public_data_normalizers import (
    NormalizedBatch,
    normalize_bus_route_stops,
    normalize_route_service_exceptions,
    normalize_scheduled_stop_times,
    normalize_scheduled_trips,
    normalize_service_calendar_exceptions,
    normalize_service_calendars,
    normalize_timetable_notices,
)
from jeju_trip.infrastructure.timetable_xlsx import (
    OfficialTimetableRawBundle,
    TimetableWorkbookRejected,
)


def _records(batch: NormalizedBatch[Any], section: str) -> tuple[Any, ...]:
    if batch.rejections:
        reason = batch.rejections[0].reason_code
        raise TimetableWorkbookRejected(f"TIMETABLE_{section}_REJECTED:{reason}")
    return batch.records


def _manifest_rows(manifest: dict[str, object], key: str) -> list[dict[str, Any]]:
    value = manifest.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TimetableWorkbookRejected(f"TIMETABLE_{key.upper()}_INVALID")
    return value


def _require_notice_review(manifest: dict[str, object], target_date: date) -> None:
    references = manifest.get("notice_source_references")
    reviewed = manifest.get("notices_reviewed_through")
    if (
        not isinstance(references, list)
        or not references
        or not all(isinstance(item, str) and item.startswith("https://") for item in references)
        or not isinstance(reviewed, str)
    ):
        raise TimetableWorkbookRejected("TIMETABLE_NOTICE_REVIEW_MISSING")
    try:
        reviewed_on = date.fromisoformat(reviewed)
    except ValueError as error:
        raise TimetableWorkbookRejected("TIMETABLE_NOTICE_REVIEW_INVALID") from error
    age_days = (target_date - reviewed_on).days
    if age_days < 0 or age_days > 45:
        raise TimetableWorkbookRejected("TIMETABLE_NOTICE_REVIEW_STALE")


def build_weekday_timetable_bundle(
    raw: OfficialTimetableRawBundle,
    target_date: date,
) -> TimetableBundle:
    """명시적 mapping만 사용해 목표 평일 하루를 위한 publication bundle을 만든다."""

    if target_date.isoweekday() > 5:
        raise TimetableWorkbookRejected("TIMETABLE_TARGET_NOT_WEEKDAY")
    _require_notice_review(raw.service_day_manifest, target_date)
    route_rows = _manifest_rows(raw.mapping_manifest, "route_stops")
    trip_rows = _manifest_rows(raw.mapping_manifest, "trip_rows")
    if not route_rows or not trip_rows:
        raise TimetableWorkbookRejected("TIMETABLE_MAPPING_ROWS_EMPTY")

    normalized_route_rows = []
    for row in route_rows:
        pattern = row.get("provider_route_pattern_id")
        stop = row.get("provider_stop_id")
        if not isinstance(pattern, str) or not pattern or not isinstance(stop, str) or not stop:
            raise TimetableWorkbookRejected("TIMETABLE_ROUTE_STOP_ID_INVALID")
        normalized_route_rows.append(
            {
                "route_fact_id": f"tago.bus-route:{pattern}",
                "stop_fact_id": f"tago.bus-stop:{stop}",
                "route_sequence": row.get("route_sequence"),
                "direction_text": row.get("direction_text"),
                "latitude": row.get("latitude"),
                "longitude": row.get("longitude"),
            }
        )

    service_id = f"weekday:{target_date.isoformat()}"
    normalized_trip_rows: list[dict[str, Any]] = []
    normalized_stop_rows: list[dict[str, Any]] = []
    for row in trip_rows:
        patterns = row.get("provider_route_pattern_ids")
        if not isinstance(patterns, list) or len(patterns) != 1 or not isinstance(patterns[0], str):
            raise TimetableWorkbookRejected("TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE")
        try:
            starts_on = date.fromisoformat(str(row.get("timetable_effective_from", "")))
            ends_on = date.fromisoformat(str(row.get("timetable_effective_to", "")))
        except ValueError as error:
            raise TimetableWorkbookRejected("TIMETABLE_EFFECTIVE_DATE_INVALID") from error
        if not starts_on <= target_date <= ends_on:
            continue
        trip_id = row.get("trip_id")
        if not isinstance(trip_id, str) or not trip_id:
            raise TimetableWorkbookRejected("TIMETABLE_TRIP_ID_INVALID")
        normalized_trip_rows.append(
            {
                "trip_id": trip_id,
                "route_fact_id": f"tago.bus-route:{patterns[0]}",
                "service_id": service_id,
                "direction_text": row.get("direction_text"),
                "timetable_effective_from": starts_on.isoformat(),
                "timetable_effective_to": ends_on.isoformat(),
            }
        )
        stop_times = row.get("major_stop_times")
        if not isinstance(stop_times, list) or len(stop_times) < 2:
            raise TimetableWorkbookRejected("TIMETABLE_MAJOR_STOP_TIMES_INSUFFICIENT")
        for stop_time in stop_times:
            if not isinstance(stop_time, dict):
                raise TimetableWorkbookRejected("TIMETABLE_MAJOR_STOP_TIME_INVALID")
            provider_stop_id = stop_time.get("provider_stop_id")
            if not isinstance(provider_stop_id, str) or not provider_stop_id:
                raise TimetableWorkbookRejected("TIMETABLE_STOP_ID_INVALID")
            normalized_stop_rows.append(
                {
                    "trip_id": trip_id,
                    "stop_id": f"tago.bus-stop:{provider_stop_id}",
                    "stop_sequence": stop_time.get("stop_sequence"),
                    "arrival_time": stop_time.get("arrival_time"),
                    "departure_time": stop_time.get("departure_time"),
                }
            )
    if not normalized_trip_rows:
        raise TimetableWorkbookRejected("TIMETABLE_TARGET_DATE_NOT_COVERED")

    route_stops = _records(normalize_bus_route_stops(normalized_route_rows), "ROUTE_STOPS")
    calendars = _records(
        normalize_service_calendars(
            [
                {
                    "service_id": service_id,
                    "day_type": "WEEKDAY",
                    "starts_on": target_date.isoformat(),
                    "ends_on": target_date.isoformat(),
                }
            ]
        ),
        "CALENDARS",
    )
    trips = _records(normalize_scheduled_trips(normalized_trip_rows), "TRIPS")
    stop_times = _records(normalize_scheduled_stop_times(normalized_stop_rows), "STOP_TIMES")
    service_exceptions = _records(
        normalize_service_calendar_exceptions(
            _manifest_rows(raw.service_day_manifest, "service_exceptions")
        ),
        "SERVICE_EXCEPTIONS",
    )
    notices = _records(
        normalize_timetable_notices(_manifest_rows(raw.service_day_manifest, "notices")),
        "NOTICES",
    )
    route_exceptions = _records(
        normalize_route_service_exceptions(
            _manifest_rows(raw.service_day_manifest, "route_exceptions")
        ),
        "ROUTE_EXCEPTIONS",
    )
    return TimetableBundle(
        route_stops=route_stops,
        service_calendars=calendars,
        trips=trips,
        stop_times=stop_times,
        service_exceptions=service_exceptions,
        notices=notices,
        route_exceptions=route_exceptions,
    )
