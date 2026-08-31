"""TourAPI와 TAGO 응답을 publication용 typed record로 정규화한다."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from jeju_trip.domain.models import Coordinates
from jeju_trip.infrastructure.normalization_spool import Rejection
from jeju_trip.planning.quality import preliminary_jeju_coordinate_check


class ProjectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TourPlaceRecord(ProjectionRecord):
    fact_id: str
    source_record_id: str
    content_type_id: str
    category_level_1: str | None = None
    category_level_2: str | None = None
    category_level_3: str | None = None
    name: str
    address: str
    position: Coordinates


class TagoBusStopRecord(ProjectionRecord):
    fact_id: str
    provider_stop_id: str
    name: str
    city_code: str
    position: Coordinates
    direction_text: str | None = None


class TagoBusRouteRecord(ProjectionRecord):
    fact_id: str
    provider_route_id: str
    route_number: str
    route_type: str
    origin_name: str
    destination_name: str
    first_departure: time | None
    last_departure: time | None
    weekday_interval_minutes: int | None
    saturday_interval_minutes: int | None
    sunday_interval_minutes: int | None


class TagoBusArrivalRecord(ProjectionRecord):
    fact_id: str
    city_code: str
    provider_stop_id: str
    provider_route_id: str
    route_number: str
    arrival_seconds: int
    remaining_stops: int
    vehicle_type: str | None = None
    checked_at: datetime
    expected_arrival_at: datetime


class HolidayRecord(ProjectionRecord):
    fact_id: str
    holiday_date: date
    name: str
    is_public_institution_holiday: bool
    date_kind: str


class JejuBoundaryRecord(ProjectionRecord):
    fact_id: str
    boundary_id: str
    name: str
    geometry: dict[str, Any]
    source_reference: str


class ScheduledStopTimeRecord(ProjectionRecord):
    fact_id: str
    trip_id: str
    stop_id: str
    stop_sequence: int
    arrival_at: time
    departure_at: time
    arrival_day_offset: int
    departure_day_offset: int


class PlaceEntranceRecord(ProjectionRecord):
    fact_id: str
    place_fact_id: str
    entrance_id: str
    entrance_type: Literal["pedestrian", "vehicle", "main", "accessible"]
    position: Coordinates
    verification_method: Literal["OFFICIAL", "MAP_POI", "ROUTE_ENDPOINT_VERIFIED", "CURATED"]
    verified_at: datetime
    expires_at: datetime
    supported_modes: tuple[Literal["walk", "bus", "taxi"], ...]
    source_reference: str


class RestaurantDietaryRecord(ProjectionRecord):
    fact_id: str
    place_fact_id: str
    menu_item_id: str
    menu_item_name: str
    verified_free_from_allergens: tuple[str, ...]
    verified_excludes_foods: tuple[str, ...]
    verification_method: Literal["OFFICIAL", "CURATED"]
    verified_at: datetime
    expires_at: datetime
    source_reference: str


class StopIdentityRecord(ProjectionRecord):
    fact_id: str
    canonical_stop_id: str
    provider: str
    provider_stop_id: str
    source_fact_id: str
    position: Coordinates
    normalized_name: str
    direction_text: str
    mapping_method: Literal["OFFICIAL_ID", "COORDINATE_AND_NAME", "ROUTE_SEQUENCE", "CURATED"]
    mapping_confidence: float
    mapping_status: Literal["CONFIRMED", "REVIEW_REQUIRED", "REJECTED"]


class BusRouteStopRecord(ProjectionRecord):
    route_fact_id: str
    stop_fact_id: str
    route_sequence: int
    direction_text: str
    position: Coordinates


class ServiceScopeMemberRecord(ProjectionRecord):
    fact_id: str
    region_code: str
    grid_id: str
    member_type: Literal["PLACE", "ENTRANCE", "STOP", "ROUTE"]
    member_id: str
    role: str
    required: bool
    source_reference: str


class ItineraryTemplateStepRecord(ProjectionRecord):
    fact_id: str
    region_code: str
    grid_id: str
    strategy: Literal["balanced", "relaxed", "experience_max"]
    sequence: int
    activity_type: Literal["visit", "meal", "rest"]
    candidate_group: str
    required: bool
    source_reference: str


class ItineraryTemplateCandidateRecord(ProjectionRecord):
    fact_id: str
    region_code: str
    grid_id: str
    candidate_group: str
    place_fact_id: str
    priority: int
    source_reference: str


class ServiceCalendarRecord(ProjectionRecord):
    fact_id: str
    service_id: str
    day_type: Literal["WEEKDAY", "SATURDAY", "SUNDAY", "HOLIDAY"]
    starts_on: date
    ends_on: date


class ScheduledTripRecord(ProjectionRecord):
    fact_id: str
    trip_id: str
    route_fact_id: str
    service_id: str
    direction_text: str
    timetable_effective_from: date
    timetable_effective_to: date


class ServiceCalendarExceptionRecord(ProjectionRecord):
    fact_id: str
    service_id: str
    exception_date: date
    exception_type: Literal["ADDED", "REMOVED"]
    holiday_name: str | None = None
    source_reference: str


class TimetableNoticeRecord(ProjectionRecord):
    fact_id: str
    provider_notice_id: str
    title: str
    published_on: date
    effective_from: date
    effective_to: date | None = None
    attachment_references: tuple[str, ...] = ()
    source_reference: str


class RouteServiceExceptionRecord(ProjectionRecord):
    fact_id: str
    route_fact_id: str
    notice_fact_id: str
    starts_at: datetime
    ends_at: datetime | None = None
    exception_type: Literal["DETOUR", "STOP_SKIPPED", "SUSPENDED", "TIMETABLE_CHANGED"]
    affected_stop_fact_ids: tuple[str, ...] = ()
    source_reference: str


@dataclass(frozen=True)
class NormalizedBatch[T]:
    records: tuple[T, ...]
    rejections: tuple[Rejection, ...]


class OpeningPeriod(ProjectionRecord):
    service_days: tuple[int, ...]
    opens_at: time
    closes_at: time
    closes_day_offset: Literal[0, 1] = 0
    last_admission_at: time | None = None
    last_order_at: time | None = None


class OpeningHoursNormalization(ProjectionRecord):
    status: Literal["VERIFIED", "PARTIAL", "UNKNOWN", "CONFLICTED"]
    raw_text: str
    periods: tuple[OpeningPeriod, ...] = ()
    break_periods: tuple[OpeningPeriod, ...] = ()


class PlaceOpeningRuleRecord(ProjectionRecord):
    fact_id: str
    place_fact_id: str
    service_day: int | None
    valid_from: date | None
    valid_to: date | None
    period_kind: Literal["OPEN", "BREAK"]
    opens_minute: int
    closes_minute: int
    closes_day_offset: Literal[0, 1]
    last_admission_minute: int | None = None
    last_order_minute: int | None = None
    source_reference: str


class PlaceScheduleExceptionRecord(ProjectionRecord):
    fact_id: str
    place_fact_id: str
    exception_date: date
    exception_type: Literal["CLOSED", "SPECIAL_HOURS"]
    opens_minute: int | None = None
    closes_minute: int | None = None
    closes_day_offset: Literal[0, 1] = 0
    source_reference: str


class PlaceWeeklyClosureRecord(ProjectionRecord):
    fact_id: str
    place_fact_id: str
    service_day: int
    valid_from: date | None = None
    valid_to: date | None = None
    source_reference: str


class PlaceOpeningObservationRecord(ProjectionRecord):
    fact_id: str
    place_fact_id: str
    observation_status: Literal["STRUCTURED", "PARTIAL", "UNVERIFIABLE", "NO_DATA"]
    reason_code: str | None = None
    source_reference: str


class NormalizationFieldError(ValueError):
    def __init__(self, field_name: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.field_name = field_name
        self.reason_code = reason_code


def _required_text(row: dict[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if value is None or not str(value).strip():
        raise NormalizationFieldError(field_name, "REQUIRED_FIELD_MISSING")
    return str(value).strip()


def _optional_text(row: dict[str, Any], field_name: str) -> str | None:
    value = row.get(field_name)
    return str(value).strip() if value is not None and str(value).strip() else None


def _position(row: dict[str, Any], latitude_field: str, longitude_field: str) -> Coordinates:
    try:
        latitude = float(_required_text(row, latitude_field))
        longitude = float(_required_text(row, longitude_field))
    except ValueError as error:
        if isinstance(error, NormalizationFieldError):
            raise
        raise NormalizationFieldError(latitude_field, "COORDINATE_INVALID") from error
    issues = preliminary_jeju_coordinate_check(latitude, longitude)
    if issues:
        raise NormalizationFieldError("position", issues[0].reason_code)
    return Coordinates(latitude=latitude, longitude=longitude)


def _normalize_rows[T](
    rows: list[dict[str, Any]],
    source_id_field: str,
    normalize: Callable[[dict[str, Any]], T],
) -> NormalizedBatch[T]:
    records: list[T] = []
    rejections: list[Rejection] = []
    for row in rows:
        source_record_id = str(row.get(source_id_field) or "") or None
        try:
            records.append(normalize(row))
        except NormalizationFieldError as error:
            rejections.append(Rejection(source_record_id, error.field_name, error.reason_code))
        except (TypeError, ValueError):
            rejections.append(Rejection(source_record_id, None, "ROW_INVALID"))
    return NormalizedBatch(tuple(records), tuple(rejections))


def normalize_tour_places(rows: list[dict[str, Any]]) -> NormalizedBatch[TourPlaceRecord]:
    def normalize(row: dict[str, Any]) -> TourPlaceRecord:
        source_record_id = _required_text(row, "contentid")
        return TourPlaceRecord(
            fact_id=f"tourapi.place:{source_record_id}",
            source_record_id=source_record_id,
            content_type_id=_required_text(row, "contenttypeid"),
            category_level_1=_optional_text(row, "cat1"),
            category_level_2=_optional_text(row, "cat2"),
            category_level_3=_optional_text(row, "cat3"),
            name=_required_text(row, "title"),
            address=_required_text(row, "addr1"),
            position=_position(row, "mapy", "mapx"),
        )

    return _normalize_rows(rows, "contentid", normalize)


def normalize_tago_stops(rows: list[dict[str, Any]]) -> NormalizedBatch[TagoBusStopRecord]:
    def normalize(row: dict[str, Any]) -> TagoBusStopRecord:
        provider_stop_id = _required_text(row, "nodeid")
        return TagoBusStopRecord(
            fact_id=f"tago.bus-stop:{provider_stop_id}",
            provider_stop_id=provider_stop_id,
            name=_required_text(row, "nodenm"),
            city_code=_required_text(row, "citycode"),
            position=_position(row, "gpslati", "gpslong"),
        )

    return _normalize_rows(rows, "nodeid", normalize)


def normalize_tago_jeju_stops(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[TagoBusStopRecord]:
    """도시코드 39로 요청한 제주 전체 정류소 응답에 요청 문맥을 명시적으로 결합한다."""

    contextualized = [{**row, "citycode": str(row.get("citycode") or "39")} for row in rows]
    return normalize_tago_stops(contextualized)


def _optional_time(value: Any, field_name: str) -> time | None:
    if value in (None, ""):
        return None
    text = str(value).strip().zfill(4)
    if not re.fullmatch(r"\d{4}", text):
        raise NormalizationFieldError(field_name, "TIME_INVALID")
    try:
        return time(int(text[:2]), int(text[2:]))
    except ValueError as error:
        raise NormalizationFieldError(field_name, "TIME_INVALID") from error


def _optional_colon_time(value: Any, field_name: str) -> time | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        raise NormalizationFieldError(field_name, "TIME_INVALID")
    try:
        return time.fromisoformat(text)
    except ValueError as error:
        raise NormalizationFieldError(field_name, "TIME_INVALID") from error


def _optional_nonnegative_int(value: Any, field_name: str) -> int:
    if value in (None, ""):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise NormalizationFieldError(field_name, "INTEGER_INVALID") from error
    if parsed < 0:
        raise NormalizationFieldError(field_name, "INTEGER_NEGATIVE")
    return parsed


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise NormalizationFieldError(field_name, "INTEGER_INVALID") from error
    if parsed <= 0:
        raise NormalizationFieldError(field_name, "INTEGER_NOT_POSITIVE")
    return parsed


def _required_nonnegative_int(row: dict[str, Any], field_name: str) -> int:
    try:
        parsed = int(_required_text(row, field_name))
    except ValueError as error:
        if isinstance(error, NormalizationFieldError):
            raise
        raise NormalizationFieldError(field_name, "INTEGER_INVALID") from error
    if parsed < 0:
        raise NormalizationFieldError(field_name, "INTEGER_NEGATIVE")
    return parsed


def normalize_tago_routes(rows: list[dict[str, Any]]) -> NormalizedBatch[TagoBusRouteRecord]:
    def normalize(row: dict[str, Any]) -> TagoBusRouteRecord:
        provider_route_id = _required_text(row, "routeid")
        return TagoBusRouteRecord(
            fact_id=f"tago.bus-route:{provider_route_id}",
            provider_route_id=provider_route_id,
            route_number=_required_text(row, "routeno"),
            route_type=_required_text(row, "routetp"),
            origin_name=_required_text(row, "startnodenm"),
            destination_name=_required_text(row, "endnodenm"),
            first_departure=_optional_time(row.get("startvehicletime"), "startvehicletime"),
            last_departure=_optional_time(row.get("endvehicletime"), "endvehicletime"),
            weekday_interval_minutes=_optional_positive_int(
                row.get("intervaltime"), "intervaltime"
            ),
            saturday_interval_minutes=_optional_positive_int(
                row.get("intervalsattime"), "intervalsattime"
            ),
            sunday_interval_minutes=_optional_positive_int(
                row.get("intervalsuntime"), "intervalsuntime"
            ),
        )

    return _normalize_rows(rows, "routeid", normalize)


def normalize_tago_route_stops(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[BusRouteStopRecord]:
    """TAGO 노선 ID별 경유 정류장 순서를 별도 snapshot으로 정규화한다."""

    def normalize(row: dict[str, Any]) -> BusRouteStopRecord:
        sequence = _required_nonnegative_int(row, "nodeord")
        if sequence == 0:
            raise NormalizationFieldError("nodeord", "INTEGER_NOT_POSITIVE")
        route_id = _required_text(row, "routeid")
        stop_id = _required_text(row, "nodeid")
        direction = _optional_text(row, "updowncd") or _optional_text(row, "nodenm")
        if direction is None:
            raise NormalizationFieldError("updowncd", "REQUIRED_FIELD_MISSING")
        return BusRouteStopRecord(
            route_fact_id=f"tago.bus-route:{route_id}",
            stop_fact_id=f"tago.bus-stop:{stop_id}",
            route_sequence=sequence,
            direction_text=direction,
            position=_position(row, "gpslati", "gpslong"),
        )

    return _normalize_rows(rows, "nodeid", normalize)


def normalize_tago_arrivals(
    rows: list[dict[str, Any]], checked_at: datetime
) -> NormalizedBatch[TagoBusArrivalRecord]:
    if checked_at.tzinfo is None:
        raise ValueError("CHECKED_AT_TIMEZONE_REQUIRED")

    def normalize(row: dict[str, Any]) -> TagoBusArrivalRecord:
        stop_id = _required_text(row, "nodeid")
        route_id = _required_text(row, "routeid")
        arrival_seconds = _required_nonnegative_int(row, "arrtime")
        remaining_stops = _required_nonnegative_int(row, "arrprevstationcnt")
        return TagoBusArrivalRecord(
            fact_id=f"tago.bus-arrival:{stop_id}:{route_id}:{int(checked_at.timestamp())}",
            city_code=_required_text(row, "citycode"),
            provider_stop_id=stop_id,
            provider_route_id=route_id,
            route_number=_required_text(row, "routeno"),
            arrival_seconds=arrival_seconds,
            remaining_stops=remaining_stops,
            vehicle_type=(str(row["vehicletp"]).strip() if row.get("vehicletp") else None),
            checked_at=checked_at,
            expected_arrival_at=checked_at + timedelta(seconds=arrival_seconds),
        )

    return _normalize_rows(rows, "nodeid", normalize)


def normalize_holidays(rows: list[dict[str, Any]]) -> NormalizedBatch[HolidayRecord]:
    def normalize(row: dict[str, Any]) -> HolidayRecord:
        raw_date = _required_text(row, "locdate")
        try:
            holiday_date = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError as error:
            raise NormalizationFieldError("locdate", "DATE_INVALID") from error
        holiday_name = _required_text(row, "dateName")
        raw_holiday = _required_text(row, "isHoliday").upper()
        if raw_holiday not in {"Y", "N"}:
            raise NormalizationFieldError("isHoliday", "BOOLEAN_INVALID")
        return HolidayRecord(
            fact_id=f"holiday.special-day:{raw_date}:{holiday_name}",
            holiday_date=holiday_date,
            name=holiday_name,
            is_public_institution_holiday=raw_holiday == "Y",
            date_kind=_required_text(row, "dateKind"),
        )

    return _normalize_rows(rows, "locdate", normalize)


def normalize_jeju_boundary(document: dict[str, Any]) -> NormalizedBatch[JejuBoundaryRecord]:
    """공식 경계를 제주 MultiPolygon projection용 단일 record로 검증한다."""

    try:
        boundary_id = _required_text(document, "boundary_id")
        name = _required_text(document, "name")
        source_reference = _official_reference(document)
        geometry = document.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in {
            "Polygon",
            "MultiPolygon",
        }:
            raise NormalizationFieldError("geometry", "BOUNDARY_GEOMETRY_INVALID")
        coordinates = geometry.get("coordinates")
        points: list[tuple[float, float]] = []

        def collect(value: Any) -> None:
            if (
                isinstance(value, list)
                and len(value) >= 2
                and all(isinstance(item, int | float) for item in value[:2])
            ):
                points.append((float(value[0]), float(value[1])))
                return
            if not isinstance(value, list):
                raise NormalizationFieldError("geometry", "BOUNDARY_COORDINATES_INVALID")
            for item in value:
                collect(item)

        collect(coordinates)
        if len(points) < 4 or any(
            not (125.0 <= longitude <= 127.5 and 32.5 <= latitude <= 34.5)
            for longitude, latitude in points
        ):
            raise NormalizationFieldError("geometry", "BOUNDARY_OUTSIDE_JEJU")
        if "제주" not in name:
            raise NormalizationFieldError("name", "BOUNDARY_NAME_NOT_JEJU")
        normalized_geometry = (
            geometry
            if geometry["type"] == "MultiPolygon"
            else {"type": "MultiPolygon", "coordinates": [coordinates]}
        )
        return NormalizedBatch(
            (
                JejuBoundaryRecord(
                    fact_id=f"spatial.jeju-boundary:{boundary_id}",
                    boundary_id=boundary_id,
                    name=name,
                    geometry=normalized_geometry,
                    source_reference=source_reference,
                ),
            ),
            (),
        )
    except (NormalizationFieldError, TypeError, ValueError) as error:
        reason = (
            error.reason_code if isinstance(error, NormalizationFieldError) else "BOUNDARY_INVALID"
        )
        return NormalizedBatch((), (Rejection(None, "geometry", reason),))


def _service_time(value: Any, field_name: str) -> tuple[time, int]:
    text = str(value or "").strip().replace(":", "")
    if not re.fullmatch(r"\d{4}", text):
        raise NormalizationFieldError(field_name, "TIME_INVALID")
    hour = int(text[:2])
    minute = int(text[2:])
    if hour > 71 or minute > 59:
        raise NormalizationFieldError(field_name, "TIME_INVALID")
    return time(hour % 24, minute), hour // 24


def normalize_scheduled_stop_times(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[ScheduledStopTimeRecord]:
    def normalize(row: dict[str, Any]) -> ScheduledStopTimeRecord:
        trip_id = _required_text(row, "trip_id")
        stop_id = _required_text(row, "stop_id")
        stop_sequence = _required_nonnegative_int(row, "stop_sequence")
        if stop_sequence == 0:
            raise NormalizationFieldError("stop_sequence", "INTEGER_NOT_POSITIVE")
        arrival_at, arrival_offset = _service_time(row.get("arrival_time"), "arrival_time")
        departure_at, departure_offset = _service_time(row.get("departure_time"), "departure_time")
        if (departure_offset, departure_at) < (arrival_offset, arrival_at):
            raise NormalizationFieldError("departure_time", "STOP_TIME_ORDER_INVALID")
        return ScheduledStopTimeRecord(
            fact_id=f"jeju.stop-time:{trip_id}:{stop_sequence}",
            trip_id=trip_id,
            stop_id=stop_id,
            stop_sequence=stop_sequence,
            arrival_at=arrival_at,
            departure_at=departure_at,
            arrival_day_offset=arrival_offset,
            departure_day_offset=departure_offset,
        )

    return _normalize_rows(rows, "trip_id", normalize)


def _iso_date(row: dict[str, Any], field_name: str) -> date:
    try:
        return date.fromisoformat(_required_text(row, field_name))
    except ValueError as error:
        if isinstance(error, NormalizationFieldError):
            raise
        raise NormalizationFieldError(field_name, "DATE_INVALID") from error


def _iso_datetime(row: dict[str, Any], field_name: str) -> datetime:
    try:
        value = datetime.fromisoformat(_required_text(row, field_name))
    except ValueError as error:
        if isinstance(error, NormalizationFieldError):
            raise
        raise NormalizationFieldError(field_name, "DATETIME_INVALID") from error
    if value.tzinfo is None:
        raise NormalizationFieldError(field_name, "DATETIME_TIMEZONE_REQUIRED")
    return value


def normalize_place_entrances(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[PlaceEntranceRecord]:
    """수동 검증 입구는 검증시각·만료·지원수단이 모두 있을 때만 승인한다."""

    def normalize(row: dict[str, Any]) -> PlaceEntranceRecord:
        entrance_id = _required_text(row, "entrance_id")
        entrance_type = _required_text(row, "entrance_type")
        verification_method = _required_text(row, "verification_method")
        modes = tuple(item.strip() for item in _required_text(row, "supported_modes").split(","))
        if entrance_type not in {"pedestrian", "vehicle", "main", "accessible"}:
            raise NormalizationFieldError("entrance_type", "ENTRANCE_TYPE_INVALID")
        if verification_method not in {
            "OFFICIAL",
            "MAP_POI",
            "ROUTE_ENDPOINT_VERIFIED",
            "CURATED",
        }:
            raise NormalizationFieldError("verification_method", "VERIFICATION_METHOD_INVALID")
        if not modes or any(mode not in {"walk", "bus", "taxi"} for mode in modes):
            raise NormalizationFieldError("supported_modes", "SUPPORTED_MODE_INVALID")
        verified_at = _iso_datetime(row, "verified_at")
        expires_at = _iso_datetime(row, "expires_at")
        if expires_at <= verified_at:
            raise NormalizationFieldError("expires_at", "VERIFICATION_EXPIRY_INVALID")
        source_reference = _required_text(row, "source_reference")
        if not source_reference.startswith(("https://", "internal://")):
            raise NormalizationFieldError("source_reference", "SOURCE_REFERENCE_INVALID")
        return PlaceEntranceRecord(
            fact_id=f"travel.place-entrance:{entrance_id}",
            place_fact_id=_required_text(row, "place_fact_id"),
            entrance_id=entrance_id,
            entrance_type=cast(
                Literal["pedestrian", "vehicle", "main", "accessible"], entrance_type
            ),
            position=_position(row, "latitude", "longitude"),
            verification_method=cast(
                Literal["OFFICIAL", "MAP_POI", "ROUTE_ENDPOINT_VERIFIED", "CURATED"],
                verification_method,
            ),
            verified_at=verified_at,
            expires_at=expires_at,
            supported_modes=cast(tuple[Literal["walk", "bus", "taxi"], ...], modes),
            source_reference=source_reference,
        )

    return _normalize_rows(rows, "entrance_id", normalize)


def normalize_restaurant_dietary_facts(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[RestaurantDietaryRecord]:
    """공식·수동 검증 메뉴의 명시적 알레르겐·제외음식 부재 주장만 정규화한다."""

    def terms(row: dict[str, Any], field_name: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item.strip().casefold()
                for item in str(row.get(field_name) or "").split("|")
                if item.strip()
            )
        )

    def normalize(row: dict[str, Any]) -> RestaurantDietaryRecord:
        dietary_fact_id = _required_text(row, "dietary_fact_id")
        place_fact_id = _required_text(row, "place_fact_id")
        if not place_fact_id.startswith("tourapi.place:"):
            raise NormalizationFieldError("place_fact_id", "PLACE_FACT_ID_INVALID")
        free_from = terms(row, "verified_free_from_allergens")
        excludes = terms(row, "verified_excludes_foods")
        if not free_from and not excludes:
            raise NormalizationFieldError(
                "verified_free_from_allergens", "DIETARY_ASSERTION_EMPTY"
            )
        verification_method = _required_text(row, "verification_method")
        if verification_method not in {"OFFICIAL", "CURATED"}:
            raise NormalizationFieldError(
                "verification_method", "VERIFICATION_METHOD_INVALID"
            )
        verified_at = _iso_datetime(row, "verified_at")
        expires_at = _iso_datetime(row, "expires_at")
        if expires_at <= verified_at:
            raise NormalizationFieldError("expires_at", "VERIFICATION_EXPIRY_INVALID")
        source_reference = _required_text(row, "source_reference")
        if not source_reference.startswith(("https://", "internal://")):
            raise NormalizationFieldError("source_reference", "SOURCE_REFERENCE_INVALID")
        return RestaurantDietaryRecord(
            fact_id=f"travel.restaurant-dietary:{dietary_fact_id}",
            place_fact_id=place_fact_id,
            menu_item_id=_required_text(row, "menu_item_id"),
            menu_item_name=_required_text(row, "menu_item_name"),
            verified_free_from_allergens=free_from,
            verified_excludes_foods=excludes,
            verification_method=cast(Literal["OFFICIAL", "CURATED"], verification_method),
            verified_at=verified_at,
            expires_at=expires_at,
            source_reference=source_reference,
        )

    return _normalize_rows(rows, "dietary_fact_id", normalize)


def normalize_stop_identities(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[StopIdentityRecord]:
    """정류장 mapping은 방향과 confidence가 있는 명시적 판정만 정규화한다."""

    def normalize(row: dict[str, Any]) -> StopIdentityRecord:
        method = _required_text(row, "mapping_method")
        status = _required_text(row, "mapping_status")
        if method not in {"OFFICIAL_ID", "COORDINATE_AND_NAME", "ROUTE_SEQUENCE", "CURATED"}:
            raise NormalizationFieldError("mapping_method", "MAPPING_METHOD_INVALID")
        if status not in {"CONFIRMED", "REVIEW_REQUIRED", "REJECTED"}:
            raise NormalizationFieldError("mapping_status", "MAPPING_STATUS_INVALID")
        try:
            confidence = float(_required_text(row, "mapping_confidence"))
        except ValueError as error:
            if isinstance(error, NormalizationFieldError):
                raise
            raise NormalizationFieldError("mapping_confidence", "CONFIDENCE_INVALID") from error
        if not 0 <= confidence <= 1:
            raise NormalizationFieldError("mapping_confidence", "CONFIDENCE_INVALID")
        canonical_id = _required_text(row, "canonical_stop_id")
        provider_stop_id = _required_text(row, "provider_stop_id")
        return StopIdentityRecord(
            fact_id=f"transport.stop-identity:{canonical_id}:{provider_stop_id}",
            canonical_stop_id=canonical_id,
            provider=_required_text(row, "provider"),
            provider_stop_id=provider_stop_id,
            source_fact_id=_required_text(row, "source_fact_id"),
            position=_position(row, "latitude", "longitude"),
            normalized_name=_required_text(row, "normalized_name"),
            direction_text=_required_text(row, "direction_text"),
            mapping_method=cast(
                Literal["OFFICIAL_ID", "COORDINATE_AND_NAME", "ROUTE_SEQUENCE", "CURATED"],
                method,
            ),
            mapping_confidence=confidence,
            mapping_status=cast(Literal["CONFIRMED", "REVIEW_REQUIRED", "REJECTED"], status),
        )

    return _normalize_rows(rows, "canonical_stop_id", normalize)


def normalize_service_calendars(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[ServiceCalendarRecord]:
    def normalize(row: dict[str, Any]) -> ServiceCalendarRecord:
        service_id = _required_text(row, "service_id")
        day_type = _required_text(row, "day_type")
        if day_type not in {"WEEKDAY", "SATURDAY", "SUNDAY", "HOLIDAY"}:
            raise NormalizationFieldError("day_type", "DAY_TYPE_INVALID")
        starts_on = _iso_date(row, "starts_on")
        ends_on = _iso_date(row, "ends_on")
        if ends_on < starts_on:
            raise NormalizationFieldError("ends_on", "DATE_RANGE_INVALID")
        return ServiceCalendarRecord(
            fact_id=f"jeju.service-calendar:{service_id}:{day_type}",
            service_id=service_id,
            day_type=cast(Literal["WEEKDAY", "SATURDAY", "SUNDAY", "HOLIDAY"], day_type),
            starts_on=starts_on,
            ends_on=ends_on,
        )

    return _normalize_rows(rows, "service_id", normalize)


def normalize_bus_route_stops(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[BusRouteStopRecord]:
    def normalize(row: dict[str, Any]) -> BusRouteStopRecord:
        sequence = _required_nonnegative_int(row, "route_sequence")
        if sequence == 0:
            raise NormalizationFieldError("route_sequence", "INTEGER_NOT_POSITIVE")
        return BusRouteStopRecord(
            route_fact_id=_required_text(row, "route_fact_id"),
            stop_fact_id=_required_text(row, "stop_fact_id"),
            route_sequence=sequence,
            direction_text=_required_text(row, "direction_text"),
            position=_position(row, "latitude", "longitude"),
        )

    return _normalize_rows(rows, "route_fact_id", normalize)


def normalize_scheduled_trips(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[ScheduledTripRecord]:
    def normalize(row: dict[str, Any]) -> ScheduledTripRecord:
        trip_id = _required_text(row, "trip_id")
        starts_on = _iso_date(row, "timetable_effective_from")
        ends_on = _iso_date(row, "timetable_effective_to")
        if ends_on < starts_on:
            raise NormalizationFieldError("timetable_effective_to", "DATE_RANGE_INVALID")
        return ScheduledTripRecord(
            fact_id=f"jeju.scheduled-trip:{trip_id}",
            trip_id=trip_id,
            route_fact_id=_required_text(row, "route_fact_id"),
            service_id=_required_text(row, "service_id"),
            direction_text=_required_text(row, "direction_text"),
            timetable_effective_from=starts_on,
            timetable_effective_to=ends_on,
        )

    return _normalize_rows(rows, "trip_id", normalize)


def _official_reference(row: dict[str, Any]) -> str:
    reference = _required_text(row, "source_reference")
    if not reference.startswith("https://"):
        raise NormalizationFieldError("source_reference", "OFFICIAL_SOURCE_REFERENCE_REQUIRED")
    return reference


def _required_bool(row: dict[str, Any], field_name: str) -> bool:
    value = _required_text(row, field_name).lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    raise NormalizationFieldError(field_name, "BOOLEAN_INVALID")


def normalize_scope_members(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[ServiceScopeMemberRecord]:
    """동부 scope의 명시적 구성원만 coverage 분모로 정규화한다."""

    def normalize(row: dict[str, Any]) -> ServiceScopeMemberRecord:
        return ServiceScopeMemberRecord(
            fact_id=_required_text(row, "fact_id"),
            region_code=_required_text(row, "region_code"),
            grid_id=_required_text(row, "grid_id"),
            member_type=_required_text(row, "member_type"),  # type: ignore[arg-type]
            member_id=_required_text(row, "member_id"),
            role=_required_text(row, "role"),
            required=_required_bool(row, "required"),
            source_reference=_required_text(row, "source_reference"),
        )

    return _normalize_rows(rows, "fact_id", normalize)


def normalize_itinerary_template_steps(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[ItineraryTemplateStepRecord]:
    """전략별 활동 순서와 candidate group을 양의 순번으로 정규화한다."""

    def normalize(row: dict[str, Any]) -> ItineraryTemplateStepRecord:
        sequence = _required_nonnegative_int(row, "sequence")
        if sequence == 0:
            raise NormalizationFieldError("sequence", "SEQUENCE_NOT_POSITIVE")
        return ItineraryTemplateStepRecord(
            fact_id=_required_text(row, "fact_id"),
            region_code=_required_text(row, "region_code"),
            grid_id=_required_text(row, "grid_id"),
            strategy=_required_text(row, "strategy"),  # type: ignore[arg-type]
            sequence=sequence,
            activity_type=_required_text(row, "activity_type"),  # type: ignore[arg-type]
            candidate_group=_required_text(row, "candidate_group"),
            required=_required_bool(row, "required"),
            source_reference=_required_text(row, "source_reference"),
        )

    return _normalize_rows(rows, "fact_id", normalize)


def normalize_itinerary_template_candidates(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[ItineraryTemplateCandidateRecord]:
    """candidate group의 장소 우선순위를 양의 값으로 정규화한다."""

    def normalize(row: dict[str, Any]) -> ItineraryTemplateCandidateRecord:
        priority = _required_nonnegative_int(row, "priority")
        if priority == 0:
            raise NormalizationFieldError("priority", "PRIORITY_NOT_POSITIVE")
        return ItineraryTemplateCandidateRecord(
            fact_id=_required_text(row, "fact_id"),
            region_code=_required_text(row, "region_code"),
            grid_id=_required_text(row, "grid_id"),
            candidate_group=_required_text(row, "candidate_group"),
            place_fact_id=_required_text(row, "place_fact_id"),
            priority=priority,
            source_reference=_required_text(row, "source_reference"),
        )

    return _normalize_rows(rows, "fact_id", normalize)


def normalize_service_calendar_exceptions(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[ServiceCalendarExceptionRecord]:
    def normalize(row: dict[str, Any]) -> ServiceCalendarExceptionRecord:
        service_id = _required_text(row, "service_id")
        exception_date = _iso_date(row, "exception_date")
        exception_type = _required_text(row, "exception_type")
        if exception_type not in {"ADDED", "REMOVED"}:
            raise NormalizationFieldError("exception_type", "EXCEPTION_TYPE_INVALID")
        return ServiceCalendarExceptionRecord(
            fact_id=f"jeju.service-exception:{service_id}:{exception_date.isoformat()}",
            service_id=service_id,
            exception_date=exception_date,
            exception_type=cast(Literal["ADDED", "REMOVED"], exception_type),
            holiday_name=str(row.get("holiday_name") or "").strip() or None,
            source_reference=_official_reference(row),
        )

    return _normalize_rows(rows, "service_id", normalize)


def normalize_timetable_notices(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[TimetableNoticeRecord]:
    def normalize(row: dict[str, Any]) -> TimetableNoticeRecord:
        notice_id = _required_text(row, "provider_notice_id")
        effective_from = _iso_date(row, "effective_from")
        effective_to = _optional_date(row, "effective_to")
        if effective_to and effective_to < effective_from:
            raise NormalizationFieldError("effective_to", "DATE_RANGE_INVALID")
        return TimetableNoticeRecord(
            fact_id=f"jeju.timetable-notice:{notice_id}",
            provider_notice_id=notice_id,
            title=_required_text(row, "title"),
            published_on=_iso_date(row, "published_on"),
            effective_from=effective_from,
            effective_to=effective_to,
            attachment_references=tuple(
                value.strip()
                for value in str(row.get("attachment_references") or "").split(",")
                if value.strip()
            ),
            source_reference=_official_reference(row),
        )

    return _normalize_rows(rows, "provider_notice_id", normalize)


def normalize_route_service_exceptions(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[RouteServiceExceptionRecord]:
    def normalize(row: dict[str, Any]) -> RouteServiceExceptionRecord:
        exception_id = _required_text(row, "exception_id")
        exception_type = _required_text(row, "exception_type")
        allowed = {"DETOUR", "STOP_SKIPPED", "SUSPENDED", "TIMETABLE_CHANGED"}
        if exception_type not in allowed:
            raise NormalizationFieldError("exception_type", "EXCEPTION_TYPE_INVALID")
        starts_at = _iso_datetime(row, "starts_at")
        raw_end = str(row.get("ends_at") or "").strip()
        ends_at = _iso_datetime(row, "ends_at") if raw_end else None
        if ends_at and ends_at < starts_at:
            raise NormalizationFieldError("ends_at", "DATETIME_RANGE_INVALID")
        return RouteServiceExceptionRecord(
            fact_id=f"jeju.route-service-exception:{exception_id}",
            route_fact_id=_required_text(row, "route_fact_id"),
            notice_fact_id=_required_text(row, "notice_fact_id"),
            starts_at=starts_at,
            ends_at=ends_at,
            exception_type=cast(
                Literal["DETOUR", "STOP_SKIPPED", "SUSPENDED", "TIMETABLE_CHANGED"],
                exception_type,
            ),
            affected_stop_fact_ids=tuple(
                value.strip()
                for value in str(row.get("affected_stop_fact_ids") or "").split(",")
                if value.strip()
            ),
            source_reference=_official_reference(row),
        )

    return _normalize_rows(rows, "exception_id", normalize)


def _optional_date(row: dict[str, Any], field_name: str) -> date | None:
    value = str(row.get(field_name) or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise NormalizationFieldError(field_name, "DATE_INVALID") from error


def _optional_clock_minute(row: dict[str, Any], field_name: str) -> int | None:
    value = str(row.get(field_name) or "").strip()
    if not value:
        return None
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise NormalizationFieldError(field_name, "TIME_INVALID") from error
    return parsed.hour * 60 + parsed.minute


def normalize_place_opening_rules(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[PlaceOpeningRuleRecord]:
    """공식 원문에서 사람이 검증한 날짜·요일별 운영 구간만 승인한다."""

    def normalize(row: dict[str, Any]) -> PlaceOpeningRuleRecord:
        rule_id = _required_text(row, "rule_id")
        raw_day = str(row.get("service_day") or "").strip()
        service_day = int(raw_day) if raw_day else None
        if service_day is not None and not 1 <= service_day <= 7:
            raise NormalizationFieldError("service_day", "SERVICE_DAY_INVALID")
        period_kind = _required_text(row, "period_kind")
        if period_kind not in {"OPEN", "BREAK"}:
            raise NormalizationFieldError("period_kind", "PERIOD_KIND_INVALID")
        opens = _optional_clock_minute(row, "opens_at")
        closes = _optional_clock_minute(row, "closes_at")
        if opens is None or closes is None:
            raise NormalizationFieldError("opens_at", "TIME_REQUIRED")
        day_offset = int(str(row.get("closes_day_offset") or "0"))
        if day_offset not in {0, 1} or (day_offset == 0 and closes <= opens):
            raise NormalizationFieldError("closes_at", "OPENING_RANGE_INVALID")
        valid_from = _optional_date(row, "valid_from")
        valid_to = _optional_date(row, "valid_to")
        if valid_from and valid_to and valid_to < valid_from:
            raise NormalizationFieldError("valid_to", "DATE_RANGE_INVALID")
        source_reference = _required_text(row, "source_reference")
        if not source_reference.startswith("https://"):
            raise NormalizationFieldError("source_reference", "OFFICIAL_SOURCE_REFERENCE_REQUIRED")
        return PlaceOpeningRuleRecord(
            fact_id=f"travel.place-opening-rule:{rule_id}",
            place_fact_id=_required_text(row, "place_fact_id"),
            service_day=service_day,
            valid_from=valid_from,
            valid_to=valid_to,
            period_kind=cast(Literal["OPEN", "BREAK"], period_kind),
            opens_minute=opens,
            closes_minute=closes,
            closes_day_offset=cast(Literal[0, 1], day_offset),
            last_admission_minute=_optional_clock_minute(row, "last_admission_at"),
            last_order_minute=_optional_clock_minute(row, "last_order_at"),
            source_reference=source_reference,
        )

    return _normalize_rows(rows, "rule_id", normalize)


def normalize_place_schedule_exceptions(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[PlaceScheduleExceptionRecord]:
    """날짜별 휴무와 특별 운영시간을 기본 영업 규칙과 분리해 승인한다."""

    def normalize(row: dict[str, Any]) -> PlaceScheduleExceptionRecord:
        exception_id = _required_text(row, "exception_id")
        exception_type = _required_text(row, "exception_type")
        if exception_type not in {"CLOSED", "SPECIAL_HOURS"}:
            raise NormalizationFieldError("exception_type", "EXCEPTION_TYPE_INVALID")
        opens = _optional_clock_minute(row, "opens_at")
        closes = _optional_clock_minute(row, "closes_at")
        day_offset = int(str(row.get("closes_day_offset") or "0"))
        if day_offset not in {0, 1}:
            raise NormalizationFieldError("closes_day_offset", "DAY_OFFSET_INVALID")
        if exception_type == "SPECIAL_HOURS" and (
            opens is None or closes is None or (day_offset == 0 and closes <= opens)
        ):
            raise NormalizationFieldError("opens_at", "SPECIAL_HOURS_INVALID")
        if exception_type == "CLOSED" and (opens is not None or closes is not None):
            raise NormalizationFieldError("opens_at", "CLOSED_HOURS_FORBIDDEN")
        source_reference = _required_text(row, "source_reference")
        if not source_reference.startswith("https://"):
            raise NormalizationFieldError("source_reference", "OFFICIAL_SOURCE_REFERENCE_REQUIRED")
        return PlaceScheduleExceptionRecord(
            fact_id=f"travel.place-schedule-exception:{exception_id}",
            place_fact_id=_required_text(row, "place_fact_id"),
            exception_date=_iso_date(row, "exception_date"),
            exception_type=cast(Literal["CLOSED", "SPECIAL_HOURS"], exception_type),
            opens_minute=opens,
            closes_minute=closes,
            closes_day_offset=cast(Literal[0, 1], day_offset),
            source_reference=source_reference,
        )

    return _normalize_rows(rows, "exception_id", normalize)


EXPLICIT_DAILY_HOURS = re.compile(
    r"^\s*매일\s+(\d{2}):(\d{2})\s*[~～-]\s*(\d{2}):(\d{2})"
    r"(?:\s*\(입장마감\s*(\d{2}):(\d{2})\))?\s*$"
)

DAY_NUMBER = {"월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6, "일": 7}
DAY_TOKEN_PATTERN = r"[월화수목금토일](?:요일)?"
LABELED_HOURS = re.compile(
    rf"(?<![0-9가-힣])(?P<label>매일|평일|주말|{DAY_TOKEN_PATTERN}"
    rf"(?:\s*[~～-]\s*{DAY_TOKEN_PATTERN})?)\s+"
    r"(?P<open>\d{2}:\d{2})\s*[~～-]\s*(?P<close>\d{2}:\d{2})"
)
BREAK_HOURS = re.compile(
    r"브레이크\s*타임\s*(?P<open>\d{2}:\d{2})\s*[~～-]\s*(?P<close>\d{2}:\d{2})"
)
LAST_ADMISSION = re.compile(r"(?:입장\s*마감|마지막\s*입장)\s*(\d{2}:\d{2})")
LAST_ORDER = re.compile(r"(?:라스트\s*오더|마지막\s*주문)\s*(\d{2}:\d{2})")
UNLABELED_HOURS = re.compile(
    r"(?<!\d)(?P<open>\d{2}:\d{2})\s*[~～-]\s*(?P<close>\d{2}:\d{2})(?!\d)"
)
ALWAYS_AVAILABLE = re.compile(
    r"(?:상시(?:\s*이용)?\s*가능|상시\s*개방|연중(?:\s*이용)?\s*가능|"
    r"연중\s*개방|24\s*시간(?:\s*이용)?\s*가능)"
)
EXPLICIT_DAILY_SCOPE = re.compile(
    r"(?:\(\s*매일(?:\s*운영)?\s*\)|매일\s*운영(?:\s|$))"
)
ANNUAL_OPEN = re.compile(r"(?:연중\s*무휴|무휴|^없(?:음|슴)?$)")
WEEKLY_CLOSED_DAYS = re.compile(
    rf"^매주\s*{DAY_TOKEN_PATTERN}"
    rf"(?:\s*(?:,|·|/|및|와|과)\s*{DAY_TOKEN_PATTERN})*\s*(?:휴무)?$"
)


def _clock(text: str) -> time | None:
    try:
        return time.fromisoformat(text)
    except ValueError:
        return None


def _service_days(label: str) -> tuple[int, ...]:
    if label == "매일":
        return (1, 2, 3, 4, 5, 6, 7)
    if label == "평일":
        return (1, 2, 3, 4, 5)
    if label == "주말":
        return (6, 7)
    labels = tuple(value.removesuffix("요일") for value in re.split(r"\s*[~～-]\s*", label))
    if len(labels) == 1:
        return (DAY_NUMBER[labels[0]],)
    start, end = DAY_NUMBER[labels[0]], DAY_NUMBER[labels[1]]
    if end >= start:
        return tuple(range(start, end + 1))
    return tuple((*range(start, 8), *range(1, end + 1)))


def _default_service_days_from_rest_dates(rest_values: tuple[str, ...]) -> tuple[int, ...] | None:
    if len(rest_values) != 1:
        return None
    rest_date = rest_values[0].strip()
    if ANNUAL_OPEN.search(rest_date):
        return (1, 2, 3, 4, 5, 6, 7)
    if rest_date == "주말":
        return (1, 2, 3, 4, 5)
    weekly_closed = WEEKLY_CLOSED_DAYS.fullmatch(rest_date)
    if weekly_closed is None:
        return None
    closed_days = {
        DAY_NUMBER[value.removesuffix("요일")]
        for value in re.findall(DAY_TOKEN_PATTERN, rest_date.removeprefix("매주"))
    }
    return tuple(day for day in range(1, 8) if day not in closed_days)


def _weekly_closed_days_from_rest_dates(rest_values: tuple[str, ...]) -> tuple[int, ...]:
    if len(rest_values) != 1:
        return ()
    rest_date = rest_values[0].strip()
    if rest_date == "주말":
        return (6, 7)
    if WEEKLY_CLOSED_DAYS.fullmatch(rest_date) is None:
        return ()
    return tuple(
        sorted(
            {
                DAY_NUMBER[value.removesuffix("요일")]
                for value in re.findall(DAY_TOKEN_PATTERN, rest_date.removeprefix("매주"))
            }
        )
    )


def normalize_tour_opening_hours(
    raw_text: str,
    *,
    default_service_days: tuple[int, ...] | None = None,
) -> OpeningHoursNormalization:
    normalized_text = html.unescape(raw_text)
    normalized_text = re.sub(r"<br\s*/?>", "\n", normalized_text, flags=re.IGNORECASE)
    normalized_text = re.sub(r"<[^>]+>", " ", normalized_text)
    seasonal = any(
        marker in normalized_text
        for marker in ("하절기", "동절기", "성수기", "비수기", "일출", "일몰", "계절", "시즌")
    ) or bool(
        re.search(
            r"(?:\d{1,2}\s*월?\s*[~～-]\s*\d{1,2}\s*월|"
            r"\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*[~～-]\s*"
            r"\d{4}[./-]\d{1,2}[./-]\d{1,2})",
            normalized_text,
        )
    )
    last_admission_match = LAST_ADMISSION.search(normalized_text)
    last_order_match = LAST_ORDER.search(normalized_text)
    last_admission = _clock(last_admission_match[1]) if last_admission_match else None
    last_order = _clock(last_order_match[1]) if last_order_match else None
    periods: list[OpeningPeriod] = []
    for matched in LABELED_HOURS.finditer(normalized_text):
        prefix = normalized_text[max(0, matched.start() - 12) : matched.start()]
        if "브레이크" in prefix:
            continue
        opens_at = _clock(matched["open"])
        closes_at = _clock(matched["close"])
        if opens_at is None or closes_at is None or opens_at == closes_at:
            return OpeningHoursNormalization(status="CONFLICTED", raw_text=raw_text)
        day_offset: Literal[0, 1] = 1 if closes_at < opens_at else 0
        periods.append(
            OpeningPeriod(
                service_days=_service_days(matched["label"]),
                opens_at=opens_at,
                closes_at=closes_at,
                closes_day_offset=day_offset,
                last_admission_at=last_admission,
                last_order_at=last_order,
            )
        )
    breaks: list[OpeningPeriod] = []
    for matched in BREAK_HOURS.finditer(normalized_text):
        opens_at = _clock(matched["open"])
        closes_at = _clock(matched["close"])
        if opens_at is None or closes_at is None or closes_at <= opens_at:
            return OpeningHoursNormalization(status="CONFLICTED", raw_text=raw_text)
        breaks.append(
            OpeningPeriod(
                service_days=default_service_days or (1, 2, 3, 4, 5, 6, 7),
                opens_at=opens_at,
                closes_at=closes_at,
            )
        )
    if not periods:
        unlabeled_ranges = tuple(UNLABELED_HOURS.finditer(normalized_text))
        has_clock_range = bool(unlabeled_ranges)
        inferred_service_days = default_service_days
        if inferred_service_days is None and EXPLICIT_DAILY_SCOPE.search(normalized_text):
            inferred_service_days = (1, 2, 3, 4, 5, 6, 7)
        if not seasonal and inferred_service_days:
            if ALWAYS_AVAILABLE.search(normalized_text) and not has_clock_range:
                periods.append(
                    OpeningPeriod(
                        service_days=inferred_service_days,
                        opens_at=time(0, 0),
                        closes_at=time(0, 0),
                        closes_day_offset=1,
                    )
                )
            else:
                candidate_ranges = []
                for matched in unlabeled_ranges:
                    prefix = normalized_text[max(0, matched.start() - 16) : matched.start()]
                    if "브레이크" in prefix:
                        continue
                    candidate_ranges.append(matched)
                if len(candidate_ranges) == 1:
                    matched = candidate_ranges[0]
                    opens_at = _clock(matched["open"])
                    closes_at = _clock(matched["close"])
                    if opens_at is None or closes_at is None or opens_at == closes_at:
                        return OpeningHoursNormalization(status="CONFLICTED", raw_text=raw_text)
                    periods.append(
                        OpeningPeriod(
                            service_days=inferred_service_days,
                            opens_at=opens_at,
                            closes_at=closes_at,
                            closes_day_offset=1 if closes_at < opens_at else 0,
                            last_admission_at=last_admission,
                            last_order_at=last_order,
                        )
                    )
        if periods:
            return OpeningHoursNormalization(
                status="VERIFIED",
                raw_text=raw_text,
                periods=tuple(periods),
                break_periods=tuple(breaks),
            )
        return OpeningHoursNormalization(
            status="PARTIAL" if seasonal and has_clock_range else "UNKNOWN",
            raw_text=raw_text,
        )
    return OpeningHoursNormalization(
        status="PARTIAL" if seasonal else "VERIFIED",
        raw_text=raw_text,
        periods=tuple(periods),
        break_periods=tuple(breaks),
    )


def normalize_tour_intro_opening_rules(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[PlaceOpeningRuleRecord]:
    """TourAPI 콘텐츠 유형별 운영시간 필드를 검증된 장소 운영 규칙으로 변환한다."""

    field_names = (
        "usetime",
        "usetimeculture",
        "usetimefestival",
        "usetimeleports",
        "usetimefood",
        "opentime",
        "opentimefood",
    )
    rest_field_names = (
        "restdate",
        "restdateculture",
        "restdatefestival",
        "restdateleports",
        "restdatefood",
        "restdateshopping",
    )
    records: list[PlaceOpeningRuleRecord] = []
    rejections: list[Rejection] = []
    for row in rows:
        content_id = str(row.get("contentid") or "").strip()
        raw_values = tuple(
            dict.fromkeys(
                str(row[name]).strip() for name in field_names if str(row.get(name) or "").strip()
            )
        )
        raw_text = raw_values[0] if len(raw_values) == 1 else ""
        if len(raw_values) > 1:
            rejections.append(
                Rejection(content_id or None, "opening_hours", "OPENING_HOURS_FIELDS_CONFLICTED")
            )
            continue
        if not content_id or not raw_text:
            rejections.append(
                Rejection(content_id or None, "opening_hours", "REQUIRED_FIELD_MISSING")
            )
            continue
        valid_from: date | None = None
        valid_to: date | None = None
        if str(row.get("contenttypeid") or "").strip() == "15":
            raw_start = str(row.get("eventstartdate") or "").strip()
            raw_end = str(row.get("eventenddate") or "").strip()
            if not raw_start or not raw_end:
                rejections.append(
                    Rejection(content_id, "opening_hours", "EVENT_DATE_RANGE_REQUIRED")
                )
                continue
            try:
                valid_from = datetime.strptime(raw_start, "%Y%m%d").date()
                valid_to = datetime.strptime(raw_end, "%Y%m%d").date()
            except ValueError:
                rejections.append(
                    Rejection(content_id, "opening_hours", "EVENT_DATE_RANGE_INVALID")
                )
                continue
            if valid_to < valid_from:
                rejections.append(
                    Rejection(content_id, "opening_hours", "EVENT_DATE_RANGE_INVALID")
                )
                continue
        rest_values = tuple(
            dict.fromkeys(
                str(row[name]).strip()
                for name in rest_field_names
                if str(row.get(name) or "").strip()
            )
        )
        default_service_days = _default_service_days_from_rest_dates(rest_values)
        normalized = normalize_tour_opening_hours(
            raw_text,
            default_service_days=default_service_days,
        )
        if normalized.status != "VERIFIED":
            rejections.append(Rejection(content_id, "opening_hours", "OPENING_HOURS_UNVERIFIED"))
            continue
        place_fact_id = f"tourapi.place:{content_id}"
        for period_kind, periods in (
            ("OPEN", normalized.periods),
            ("BREAK", normalized.break_periods),
        ):
            for period_index, period in enumerate(periods, start=1):
                for service_day in period.service_days:
                    rule_id = f"{content_id}:{period_kind}:{period_index}:{service_day}"
                    records.append(
                        PlaceOpeningRuleRecord(
                            fact_id=f"tourapi.place-intro:{rule_id}",
                            place_fact_id=place_fact_id,
                            service_day=service_day,
                            valid_from=valid_from,
                            valid_to=valid_to,
                            period_kind=cast(Literal["OPEN", "BREAK"], period_kind),
                            opens_minute=period.opens_at.hour * 60 + period.opens_at.minute,
                            closes_minute=period.closes_at.hour * 60 + period.closes_at.minute,
                            closes_day_offset=period.closes_day_offset,
                            last_admission_minute=(
                                period.last_admission_at.hour * 60 + period.last_admission_at.minute
                                if period.last_admission_at
                                else None
                            ),
                            last_order_minute=(
                                period.last_order_at.hour * 60 + period.last_order_at.minute
                                if period.last_order_at
                                else None
                            ),
                            source_reference=("https://www.data.go.kr/data/15101578/openapi.do"),
                        )
                    )
    return NormalizedBatch(tuple(records), tuple(rejections))


def normalize_tour_intro_weekly_closures(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[PlaceWeeklyClosureRecord]:
    """TourAPI의 명시적 반복 휴무 요일을 날짜 예외와 분리해 보존한다."""

    rest_field_names = (
        "restdate",
        "restdateculture",
        "restdatefestival",
        "restdateleports",
        "restdatefood",
        "restdateshopping",
    )
    records: list[PlaceWeeklyClosureRecord] = []
    for row in rows:
        content_id = str(row.get("contentid") or "").strip()
        if not content_id:
            continue
        rest_values = tuple(
            dict.fromkeys(
                str(row.get(field) or "").strip()
                for field in rest_field_names
                if str(row.get(field) or "").strip()
            )
        )
        for service_day in _weekly_closed_days_from_rest_dates(rest_values):
            records.append(
                PlaceWeeklyClosureRecord(
                    fact_id=f"tourapi.place-intro:{content_id}:CLOSED:{service_day}",
                    place_fact_id=f"tourapi.place:{content_id}",
                    service_day=service_day,
                    source_reference="https://www.data.go.kr/data/15101578/openapi.do",
                )
            )
    return NormalizedBatch(tuple(records), ())


def normalize_tour_intro_opening_facts(
    rows: list[dict[str, Any]],
) -> NormalizedBatch[PlaceOpeningRuleRecord | PlaceWeeklyClosureRecord]:
    """TourAPI 운영 규칙과 반복 휴무를 하나의 raw 정규화 결과로 결합한다."""

    rules = normalize_tour_intro_opening_rules(rows)
    closures = normalize_tour_intro_weekly_closures(rows)
    return NormalizedBatch((*rules.records, *closures.records), rules.rejections)


def normalize_tour_intro_opening_snapshot(
    rows: list[dict[str, Any]],
    queries: tuple[dict[str, str], ...],
) -> NormalizedBatch[
    PlaceOpeningRuleRecord | PlaceWeeklyClosureRecord | PlaceOpeningObservationRecord
]:
    """완료 query별 운영시간 구조화 결과를 원문 없이 하나의 관측 fact로 보존한다."""

    scope: dict[str, str] = {}
    for query in queries:
        content_id = str(query.get("contentId") or "").strip()
        content_type_id = str(query.get("contentTypeId") or "").strip()
        if not content_id or not content_type_id or content_id in scope:
            raise ValueError("TOUR_INTRO_QUERY_SCOPE_INVALID")
        scope[content_id] = content_type_id

    rows_by_content_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        content_id = str(row.get("contentid") or "").strip()
        content_type_id = str(row.get("contenttypeid") or "").strip()
        if content_id not in scope or content_type_id != scope[content_id]:
            raise ValueError("TOUR_INTRO_RESPONSE_SCOPE_MISMATCH")
        if content_id in rows_by_content_id:
            raise ValueError("TOUR_INTRO_RESPONSE_DUPLICATED")
        rows_by_content_id[content_id] = row

    rules = normalize_tour_intro_opening_rules(rows)
    closures = normalize_tour_intro_weekly_closures(rows)
    fact_place_ids = {
        record.place_fact_id for record in (*rules.records, *closures.records)
    }
    rejection_by_content_id = {
        str(rejection.source_record_id): rejection.reason_code
        for rejection in rules.rejections
        if rejection.source_record_id is not None
    }
    observations: list[PlaceOpeningObservationRecord] = []
    for content_id in scope:
        place_fact_id = f"tourapi.place:{content_id}"
        reason_code = rejection_by_content_id.get(content_id)
        has_row = content_id in rows_by_content_id
        has_fact = place_fact_id in fact_place_ids
        status: Literal["STRUCTURED", "PARTIAL", "UNVERIFIABLE", "NO_DATA"]
        if not has_row:
            status = "NO_DATA"
            reason_code = "SOURCE_RESPONSE_EMPTY"
        elif has_fact and reason_code is not None:
            status = "PARTIAL"
        elif has_fact:
            status = "STRUCTURED"
        else:
            status = "UNVERIFIABLE"
            reason_code = reason_code or "OPENING_HOURS_UNVERIFIED"
        observations.append(
            PlaceOpeningObservationRecord(
                fact_id=f"tourapi.place-intro:{content_id}:OBSERVATION",
                place_fact_id=place_fact_id,
                observation_status=status,
                reason_code=reason_code,
                source_reference="https://www.data.go.kr/data/15101578/openapi.do",
            )
        )
    return NormalizedBatch(
        (*rules.records, *closures.records, *observations),
        rules.rejections,
    )
