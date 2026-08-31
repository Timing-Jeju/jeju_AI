"""검증된 장소·노선·공휴일을 append-only active publication으로 발행한다."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from jeju_trip.infrastructure.public_data_normalizers import (
    BusRouteStopRecord,
    HolidayRecord,
    ItineraryTemplateCandidateRecord,
    ItineraryTemplateStepRecord,
    JejuBoundaryRecord,
    PlaceEntranceRecord,
    PlaceOpeningObservationRecord,
    PlaceOpeningRuleRecord,
    PlaceScheduleExceptionRecord,
    PlaceWeeklyClosureRecord,
    RestaurantDietaryRecord,
    RouteServiceExceptionRecord,
    ScheduledStopTimeRecord,
    ScheduledTripRecord,
    ServiceCalendarExceptionRecord,
    ServiceCalendarRecord,
    ServiceScopeMemberRecord,
    StopIdentityRecord,
    TagoBusRouteRecord,
    TimetableNoticeRecord,
    TourPlaceRecord,
)
from jeju_trip.planning.policy import BusFarePolicy, TaxiFarePolicy


@dataclass(frozen=True)
class ProjectionPublication:
    publication_id: UUID
    source_id: str
    dataset_version: str
    published_at: datetime
    record_count: int
    created: bool


@dataclass(frozen=True)
class SourceCoverageRecord:
    capability: str
    coverage_ratio: float
    region_code: str = "JEJU_ALL"
    grid_id: str = "ALL"
    service_date_from: date | None = None
    service_date_to: date | None = None
    blocking_reason: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.coverage_ratio <= 1:
            raise ValueError("COVERAGE_RATIO_INVALID")
        if not self.region_code.strip() or not self.grid_id.strip():
            raise ValueError("COVERAGE_SCOPE_REQUIRED")
        if (self.service_date_from is None) != (self.service_date_to is None):
            raise ValueError("COVERAGE_DATE_RANGE_INCOMPLETE")
        if (
            self.service_date_from is not None
            and self.service_date_to is not None
            and self.service_date_to < self.service_date_from
        ):
            raise ValueError("COVERAGE_DATE_RANGE_INVALID")
        if self.coverage_ratio < 1 and not (self.blocking_reason or "").strip():
            raise ValueError("COVERAGE_BLOCKING_REASON_REQUIRED")


CAPABILITY_SOURCE_IDS: dict[str, frozenset[str]] = {
    "service_area_ready": frozenset({"spatial.jeju-boundary"}),
    "place_search_ready": frozenset({"tourapi.place"}),
    "opening_hours_ready": frozenset({"tourapi.place-intro", "travel.place-hours-map"}),
    "opening_hours_snapshot_ready": frozenset({"tourapi.place-intro"}),
    "future_bus_planning_ready": frozenset({"jeju.bus-timetable"}),
    "verified_entrances_ready": frozenset({"travel.place-entrance-map"}),
    "confirmed_stop_mapping_ready": frozenset({"transport.stop-identity-map"}),
    "bus_fare_policy_ready": frozenset({"jeju.bus-fare-policy"}),
    "taxi_fare_policy_ready": frozenset({"jeju.taxi-fare-policy"}),
    "fare_policy_ready": frozenset({"jeju.bus-fare-policy", "jeju.taxi-fare-policy"}),
    "holiday_calendar_ready": frozenset({"holiday.special-day"}),
    "bus_route_catalog_ready": frozenset({"tago.bus-route"}),
    "bus_route_stop_catalog_ready": frozenset({"tago.bus-route-stops"}),
    "bus_stop_catalog_ready": frozenset({"tago.bus-stop"}),
    "restaurant_recommendation_ready": frozenset({"travel.restaurant-dietary-map"}),
    "cafe_recommendation_ready": frozenset(
        {"tourapi.place", "tourapi.place-intro", "travel.place-hours-map"}
    ),
    "accessibility_ready": frozenset({"travel.place-entrance-map"}),
    "scope_manifest_ready": frozenset({"travel.service-scope-manifest"}),
}


def validate_coverage_source(source_id: str, records: Iterable[SourceCoverageRecord]) -> None:
    """capability를 근거와 무관한 publication에 붙이는 것을 막는다."""

    for record in records:
        if source_id not in CAPABILITY_SOURCE_IDS.get(record.capability, frozenset()):
            raise ValueError(f"COVERAGE_SOURCE_MISMATCH:{record.capability}")


def validate_activation_coverage(
    source_id: str, records: Iterable[SourceCoverageRecord]
) -> None:
    """상세소개는 완전 snapshot으로 활성화하고 정확도 미달은 품질 지표로 보존한다."""

    materialized = tuple(records)
    validate_coverage_source(source_id, materialized)
    if source_id == "tourapi.place-intro":
        snapshot = tuple(
            record
            for record in materialized
            if record.capability == "opening_hours_snapshot_ready"
        )
        if len(snapshot) != 1 or any(
            record.coverage_ratio != 1
            or record.blocking_reason
            or record.region_code != "JEJU_ALL"
            or record.grid_id != "ALL"
            or record.service_date_from is not None
            or record.service_date_to is not None
            for record in snapshot
        ):
            raise ValueError("SNAPSHOT_COVERAGE_NOT_READY_FOR_ACTIVATION")
        return
    if any(record.coverage_ratio != 1 or record.blocking_reason for record in materialized):
        raise ValueError("COVERAGE_NOT_READY_FOR_ACTIVATION")


def _validate_appended_coverage(
    source_id: str, records: tuple[SourceCoverageRecord, ...]
) -> None:
    validate_coverage_source(source_id, records)
    if source_id == "tourapi.place-intro" and all(
        record.capability == "opening_hours_ready" for record in records
    ):
        return
    if any(record.coverage_ratio != 1 or record.blocking_reason for record in records):
        raise ValueError("COVERAGE_NOT_READY_FOR_ACTIVATION")


@dataclass(frozen=True)
class TimetableBundle:
    route_stops: tuple[BusRouteStopRecord, ...]
    service_calendars: tuple[ServiceCalendarRecord, ...]
    trips: tuple[ScheduledTripRecord, ...]
    stop_times: tuple[ScheduledStopTimeRecord, ...]
    service_exceptions: tuple[ServiceCalendarExceptionRecord, ...] = ()
    notices: tuple[TimetableNoticeRecord, ...] = ()
    route_exceptions: tuple[RouteServiceExceptionRecord, ...] = ()


@dataclass(frozen=True)
class OpeningHoursBundle:
    rules: tuple[PlaceOpeningRuleRecord, ...]
    exceptions: tuple[PlaceScheduleExceptionRecord, ...]
    weekly_closures: tuple[PlaceWeeklyClosureRecord, ...] = ()
    observations: tuple[PlaceOpeningObservationRecord, ...] = ()


@dataclass(frozen=True)
class ScopeManifestBundle:
    members: tuple[ServiceScopeMemberRecord, ...]
    steps: tuple[ItineraryTemplateStepRecord, ...]
    candidates: tuple[ItineraryTemplateCandidateRecord, ...]


@dataclass(frozen=True)
class _PublicationBasis:
    source_id: str
    publication_id: UUID
    data_as_of: date
    observed_at: datetime


class PostgresProjectionPublisher:
    """공통 publication state transition 안에서 projection별 typed insert를 수행한다."""

    def __init__(self, importer_dsn: str, assume_role: str | None = None) -> None:
        self._importer_dsn = importer_dsn
        self._assume_role = assume_role

    def publish_places(
        self, acquisition_id: UUID, records: Iterable[TourPlaceRecord]
    ) -> ProjectionPublication:
        materialized = tuple(records)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.place_fact(
                           publication_id, source_id, fact_id, source_record_id,
                           data_as_of, observed_at, name, category, address, position, attributes
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)""",
                    [
                        (
                            basis.publication_id,
                            basis.source_id,
                            record.fact_id,
                            record.source_record_id,
                            basis.data_as_of,
                            basis.observed_at,
                            record.name,
                            record.content_type_id,
                            record.address,
                            record.position.longitude,
                            record.position.latitude,
                            Jsonb(
                                {
                                    "content_type_id": record.content_type_id,
                                    "category_level_1": record.category_level_1,
                                    "category_level_2": record.category_level_2,
                                    "category_level_3": record.category_level_3,
                                }
                            ),
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_boundary(
        self, acquisition_id: UUID, records: Iterable[JejuBoundaryRecord]
    ) -> ProjectionPublication:
        materialized = tuple(records)
        if len(materialized) != 1:
            raise ValueError("BOUNDARY_SINGLE_RECORD_REQUIRED")

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.service_area_boundary(
                           publication_id, fact_id, boundary_id, name, geometry, source_refs)
                       VALUES (%s, %s, %s, %s,
                           ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s)""",
                    [
                        (
                            basis.publication_id,
                            record.fact_id,
                            record.boundary_id,
                            record.name,
                            Jsonb(record.geometry),
                            Jsonb([record.source_reference]),
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_taxi_fare_policy(
        self, acquisition_id: UUID, records: Iterable[TaxiFarePolicy]
    ) -> ProjectionPublication:
        policies = tuple(records)
        if len(policies) != 1:
            raise ValueError("TAXI_FARE_POLICY_SINGLE_RECORD_REQUIRED")
        policy = policies[0]

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.taxi_fare_policy_fact(
                           publication_id, fact_id, vehicle_type, effective_from, effective_to,
                           base_fare_krw, base_distance_meters, distance_unit_meters,
                           distance_unit_fare_krw, time_speed_threshold_kph, time_unit_seconds,
                           time_unit_fare_krw, long_distance_threshold_meters,
                           long_distance_unit_fare_krw, night_start, night_end,
                           night_surcharge_ratio, call_fee_max_krw, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            f"jeju.taxi-fare-policy:{policy.effective_from}:{vehicle.vehicle_type}",
                            vehicle.vehicle_type,
                            policy.effective_from,
                            policy.effective_to,
                            vehicle.base_fare_krw,
                            vehicle.base_distance_meters,
                            vehicle.distance_unit_meters,
                            vehicle.distance_unit_fare_krw,
                            vehicle.time_speed_threshold_kph,
                            vehicle.time_unit_seconds,
                            vehicle.time_unit_fare_krw,
                            vehicle.long_distance_threshold_meters,
                            vehicle.long_distance_unit_fare_krw,
                            policy.night_start,
                            policy.night_end,
                            policy.night_surcharge_ratio,
                            policy.call_fee_max_krw,
                            Jsonb([policy.source_url]),
                        )
                        for vehicle in policy.vehicle_types
                    ],
                )

        return self._publish(acquisition_id, policies, insert)

    def publish_bus_fare_policy(
        self, acquisition_id: UUID, records: Iterable[BusFarePolicy]
    ) -> ProjectionPublication:
        policies = tuple(records)
        if len(policies) != 1:
            raise ValueError("BUS_FARE_POLICY_SINGLE_RECORD_REQUIRED")
        policy = policies[0]

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.bus_fare_policy_fact(
                           publication_id, fact_id, fare_class, effective_from, effective_to,
                           adult_min_krw, adult_max_krw, child_min_krw, child_max_krw, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            f"jeju.bus-fare-policy:{policy.effective_from}:{fare_class}",
                            fare_class,
                            policy.effective_from,
                            policy.effective_to,
                            fares.adult_min_krw,
                            fares.adult_max_krw,
                            fares.child_min_krw,
                            fares.child_max_krw,
                            Jsonb([policy.source_url]),
                        )
                        for fare_class, fares in (
                            ("STANDARD", policy.standard),
                            ("EXPRESS", policy.express),
                        )
                    ],
                )

        return self._publish(acquisition_id, policies, insert)

    def publish_routes(
        self, acquisition_id: UUID, records: Iterable[TagoBusRouteRecord]
    ) -> ProjectionPublication:
        materialized = tuple(records)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.bus_route_fact(
                           publication_id, source_id, fact_id, provider_route_id,
                           route_number, origin_name, destination_name, attributes
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            basis.source_id,
                            record.fact_id,
                            record.provider_route_id,
                            record.route_number,
                            record.origin_name,
                            record.destination_name,
                            Jsonb(
                                {
                                    "route_type": record.route_type,
                                    "first_departure": (
                                        record.first_departure.isoformat()
                                        if record.first_departure
                                        else None
                                    ),
                                    "last_departure": (
                                        record.last_departure.isoformat()
                                        if record.last_departure
                                        else None
                                    ),
                                    "weekday_interval_minutes": (record.weekday_interval_minutes),
                                    "saturday_interval_minutes": (record.saturday_interval_minutes),
                                    "sunday_interval_minutes": (record.sunday_interval_minutes),
                                }
                            ),
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_holidays(
        self, acquisition_id: UUID, records: Iterable[HolidayRecord]
    ) -> ProjectionPublication:
        materialized = tuple(records)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.holiday_fact(
                           publication_id, fact_id, holiday_date, name,
                           is_public_institution_holiday, date_kind, source_refs
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            record.fact_id,
                            record.holiday_date,
                            record.name,
                            record.is_public_institution_holiday,
                            record.date_kind,
                            Jsonb(
                                {
                                    "source_id": basis.source_id,
                                    "source_fact_id": record.fact_id,
                                }
                            ),
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_route_stops(
        self, acquisition_id: UUID, records: Iterable[BusRouteStopRecord]
    ) -> ProjectionPublication:
        """TAGO 경유 정류장 순서를 route publication과 분리해 발행한다."""

        materialized = tuple(records)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.bus_route_stop(
                           publication_id, route_fact_id, stop_fact_id, route_sequence,
                           direction_text, source_refs, position
                       ) VALUES (%s, %s, %s, %s, %s, %s,
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)""",
                    [
                        (
                            basis.publication_id,
                            record.route_fact_id,
                            record.stop_fact_id,
                            record.route_sequence,
                            record.direction_text,
                            Jsonb([{"source_id": basis.source_id}]),
                            record.position.longitude,
                            record.position.latitude,
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_entrances(
        self, acquisition_id: UUID, records: Iterable[PlaceEntranceRecord]
    ) -> ProjectionPublication:
        materialized = tuple(records)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.place_entrance(
                           publication_id, fact_id, place_fact_id, entrance_id,
                           entrance_type, position, verification_status, verification_method,
                           source_refs, valid_from, valid_to, last_verified_at,
                           verification_expires_at, supported_modes
                       ) VALUES (%s, %s, %s, %s, %s,
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                           'VERIFIED', %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            record.fact_id,
                            record.place_fact_id,
                            record.entrance_id,
                            record.entrance_type,
                            record.position.longitude,
                            record.position.latitude,
                            record.verification_method,
                            Jsonb({"reference": record.source_reference}),
                            record.verified_at.date(),
                            record.expires_at.date(),
                            record.verified_at,
                            record.expires_at,
                            list(record.supported_modes),
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_restaurant_dietary_facts(
        self, acquisition_id: UUID, records: Iterable[RestaurantDietaryRecord]
    ) -> ProjectionPublication:
        materialized = tuple(records)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.restaurant_dietary_fact(
                           publication_id, fact_id, place_fact_id, menu_item_id,
                           menu_item_name, verified_free_from_allergens,
                           verified_excludes_foods, verification_method, source_refs,
                           valid_from, valid_to, last_verified_at,
                           verification_expires_at
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                                 %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            record.fact_id,
                            record.place_fact_id,
                            record.menu_item_id,
                            record.menu_item_name,
                            list(record.verified_free_from_allergens),
                            list(record.verified_excludes_foods),
                            record.verification_method,
                            Jsonb({"reference": record.source_reference}),
                            record.verified_at.date(),
                            record.expires_at.date(),
                            record.verified_at,
                            record.expires_at,
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_opening_hours(
        self, acquisition_id: UUID, bundle: OpeningHoursBundle
    ) -> ProjectionPublication:
        records = (
            *bundle.rules,
            *bundle.exceptions,
            *bundle.weekly_closures,
            *bundle.observations,
        )

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.place_opening_rule(
                           publication_id, fact_id, place_fact_id, service_day,
                           valid_from, valid_to, period_kind, opens_minute, closes_minute,
                           closes_day_offset, last_admission_minute, last_order_minute,
                           normalization_status, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               'VERIFIED', %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.place_fact_id,
                            item.service_day,
                            item.valid_from,
                            item.valid_to,
                            item.period_kind,
                            item.opens_minute,
                            item.closes_minute,
                            item.closes_day_offset,
                            item.last_admission_minute,
                            item.last_order_minute,
                            Jsonb(
                                {
                                    "source_id": basis.source_id,
                                    "reference": item.source_reference,
                                }
                            ),
                        )
                        for item in bundle.rules
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.place_schedule_exception(
                           publication_id, fact_id, place_fact_id, exception_date,
                           exception_type, opens_minute, closes_minute, closes_day_offset,
                           source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.place_fact_id,
                            item.exception_date,
                            item.exception_type,
                            item.opens_minute,
                            item.closes_minute,
                            item.closes_day_offset,
                            Jsonb(
                                {
                                    "source_id": basis.source_id,
                                    "reference": item.source_reference,
                                }
                            ),
                        )
                        for item in bundle.exceptions
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.place_weekly_closure(
                           publication_id, fact_id, place_fact_id, service_day,
                           valid_from, valid_to, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.place_fact_id,
                            item.service_day,
                            item.valid_from,
                            item.valid_to,
                            Jsonb(
                                {
                                    "source_id": basis.source_id,
                                    "reference": item.source_reference,
                                }
                            ),
                        )
                        for item in bundle.weekly_closures
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.place_opening_observation(
                           publication_id, fact_id, place_fact_id, observation_status,
                           reason_code, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.place_fact_id,
                            item.observation_status,
                            item.reason_code,
                            Jsonb(
                                {
                                    "source_id": basis.source_id,
                                    "reference": item.source_reference,
                                }
                            ),
                        )
                        for item in bundle.observations
                    ],
                )

        return self._publish(acquisition_id, records, insert)

    def publish_stop_identities(
        self, acquisition_id: UUID, records: Iterable[StopIdentityRecord]
    ) -> ProjectionPublication:
        materialized = tuple(records)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.stop_identity_fact(
                           publication_id, fact_id, canonical_stop_id, provider,
                           provider_stop_id, source_fact_id, latitude, longitude,
                           normalized_name, direction_text, mapping_method,
                           mapping_confidence, mapping_status, source_refs
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            record.fact_id,
                            record.canonical_stop_id,
                            record.provider,
                            record.provider_stop_id,
                            record.source_fact_id,
                            record.position.latitude,
                            record.position.longitude,
                            record.normalized_name,
                            record.direction_text,
                            record.mapping_method,
                            record.mapping_confidence,
                            record.mapping_status,
                            Jsonb([{"source_id": basis.source_id}]),
                        )
                        for record in materialized
                    ],
                )

        return self._publish(acquisition_id, materialized, insert)

    def publish_scope_manifest(
        self, acquisition_id: UUID, bundle: ScopeManifestBundle
    ) -> ProjectionPublication:
        """scope 구성원과 세 전략 template을 같은 publication으로 발행한다."""

        records = (*bundle.members, *bundle.steps, *bundle.candidates)

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.service_scope_member(
                           publication_id, fact_id, region_code, grid_id, member_type,
                           member_id, role, required, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.region_code,
                            item.grid_id,
                            item.member_type,
                            item.member_id,
                            item.role,
                            item.required,
                            Jsonb([item.source_reference]),
                        )
                        for item in bundle.members
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.itinerary_template_step(
                           publication_id, fact_id, region_code, grid_id, strategy,
                           sequence, activity_type, candidate_group, required, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.region_code,
                            item.grid_id,
                            item.strategy,
                            item.sequence,
                            item.activity_type,
                            item.candidate_group,
                            item.required,
                            Jsonb([item.source_reference]),
                        )
                        for item in bundle.steps
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.itinerary_template_candidate(
                           publication_id, fact_id, region_code, grid_id, candidate_group,
                           place_fact_id, priority, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.region_code,
                            item.grid_id,
                            item.candidate_group,
                            item.place_fact_id,
                            item.priority,
                            Jsonb([item.source_reference]),
                        )
                        for item in bundle.candidates
                    ],
                )

        return self._publish(acquisition_id, records, insert)

    def publish_timetable(
        self, acquisition_id: UUID, bundle: TimetableBundle
    ) -> ProjectionPublication:
        record_count = (
            len(bundle.route_stops)
            + len(bundle.service_calendars)
            + len(bundle.trips)
            + len(bundle.stop_times)
            + len(bundle.service_exceptions)
            + len(bundle.notices)
            + len(bundle.route_exceptions)
        )

        def insert(connection: psycopg.Connection[Any], basis: _PublicationBasis) -> None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.bus_route_stop(
                           publication_id, route_fact_id, stop_fact_id, route_sequence,
                           direction_text, source_refs, position)
                       VALUES (%s, %s, %s, %s, %s, %s,
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)""",
                    [
                        (
                            basis.publication_id,
                            item.route_fact_id,
                            item.stop_fact_id,
                            item.route_sequence,
                            item.direction_text,
                            Jsonb([{"source_id": basis.source_id}]),
                            item.position.longitude,
                            item.position.latitude,
                        )
                        for item in bundle.route_stops
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.service_calendar(
                           publication_id, fact_id, service_id, day_type, starts_on, ends_on,
                           source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.service_id,
                            item.day_type,
                            item.starts_on,
                            item.ends_on,
                            Jsonb([{"source_id": basis.source_id}]),
                        )
                        for item in bundle.service_calendars
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.scheduled_trip(
                           publication_id, fact_id, trip_id, route_fact_id, service_id,
                           direction_text, timetable_effective_from, timetable_effective_to,
                           source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.trip_id,
                            item.route_fact_id,
                            item.service_id,
                            item.direction_text,
                            item.timetable_effective_from,
                            item.timetable_effective_to,
                            Jsonb([{"source_id": basis.source_id}]),
                        )
                        for item in bundle.trips
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.scheduled_stop_time(
                           publication_id, fact_id, trip_id, stop_fact_id, stop_sequence,
                           arrival_at, departure_at, arrival_day_offset, departure_day_offset,
                           source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.trip_id,
                            item.stop_id,
                            item.stop_sequence,
                            item.arrival_at,
                            item.departure_at,
                            item.arrival_day_offset,
                            item.departure_day_offset,
                            Jsonb([{"source_id": basis.source_id}]),
                        )
                        for item in bundle.stop_times
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.service_calendar_exception(
                           publication_id, fact_id, service_id, exception_date,
                           exception_type, holiday_name, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.service_id,
                            item.exception_date,
                            item.exception_type,
                            item.holiday_name,
                            Jsonb([item.source_reference]),
                        )
                        for item in bundle.service_exceptions
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.timetable_notice(
                           publication_id, fact_id, provider_notice_id, title, published_on,
                           effective_from, effective_to, attachment_refs, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.provider_notice_id,
                            item.title,
                            item.published_on,
                            item.effective_from,
                            item.effective_to,
                            Jsonb(item.attachment_references),
                            Jsonb([item.source_reference]),
                        )
                        for item in bundle.notices
                    ],
                )
                cursor.executemany(
                    """INSERT INTO travel_projection.route_service_exception(
                           publication_id, fact_id, route_fact_id, notice_fact_id, starts_at,
                           ends_at, exception_type, affected_stop_fact_ids, source_refs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            basis.publication_id,
                            item.fact_id,
                            item.route_fact_id,
                            item.notice_fact_id,
                            item.starts_at,
                            item.ends_at,
                            item.exception_type,
                            list(item.affected_stop_fact_ids),
                            Jsonb([item.source_reference]),
                        )
                        for item in bundle.route_exceptions
                    ],
                )

        sentinel = tuple(range(record_count))
        return self._publish(acquisition_id, sentinel, insert)

    def publish_coverage(
        self, publication_id: UUID, records: Iterable[SourceCoverageRecord]
    ) -> None:
        """coverage 1.0을 검증한 staged publication만 원자적으로 활성화한다."""

        materialized = tuple(records)
        if not materialized:
            raise ValueError("COVERAGE_RECORDS_EMPTY")
        with psycopg.connect(self._importer_dsn) as connection:
            if self._assume_role:
                connection.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._assume_role))
                )
            staged = connection.execute(
                """SELECT publication.source_id, publication.acquisition_id
                   FROM source_admin.publication publication
                   JOIN source_admin.acquisition acquisition
                     ON acquisition.acquisition_id = publication.acquisition_id
                   WHERE publication.publication_id = %s
                     AND acquisition.status = 'STAGED'""",
                (publication_id,),
            ).fetchone()
            if staged is None:
                raise ValueError("COVERAGE_PUBLICATION_NOT_STAGED")
            source_id, acquisition_id = staged
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source_id,)
            )
            connection.execute(
                """SELECT status FROM source_admin.acquisition
                   WHERE acquisition_id = %s FOR UPDATE""",
                (acquisition_id,),
            ).fetchone()
            validate_activation_coverage(str(source_id), materialized)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.source_coverage(
                           publication_id, capability, coverage_ratio, reason_code,
                           region_code, grid_id, service_date_from, service_date_to,
                           blocking_reason
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            publication_id,
                            record.capability,
                            record.coverage_ratio,
                            record.blocking_reason,
                            record.region_code,
                            record.grid_id,
                            record.service_date_from,
                            record.service_date_to,
                            record.blocking_reason,
                        )
                        for record in materialized
                    ],
                )
            previous = connection.execute(
                """SELECT publication_id FROM source_admin.active_snapshot
                   WHERE source_id = %s FOR UPDATE""",
                (source_id,),
            ).fetchone()
            activated_at = connection.execute(
                """INSERT INTO source_admin.active_snapshot(source_id, publication_id)
                   VALUES (%s, %s)
                   ON CONFLICT (source_id) DO UPDATE
                   SET publication_id = EXCLUDED.publication_id,
                       activated_at = clock_timestamp()
                   RETURNING activated_at""",
                (source_id, publication_id),
            ).fetchone()
            if activated_at is None:
                raise RuntimeError("ACTIVE_SNAPSHOT_UPDATE_FAILED")
            connection.execute(
                """INSERT INTO source_admin.activation_event(
                       source_id, previous_publication_id, publication_id, activated_at
                   ) VALUES (%s, %s, %s, %s)""",
                (
                    source_id,
                    previous[0] if previous else None,
                    publication_id,
                    activated_at[0],
                ),
            )
            updated = connection.execute(
                """UPDATE source_admin.acquisition SET status = 'PUBLISHED'
                   WHERE acquisition_id = %s AND status = 'STAGED'""",
                (acquisition_id,),
            )
            if updated.rowcount != 1:
                raise RuntimeError("PUBLICATION_STATE_TRANSITION_FAILED")

    def append_active_coverage(
        self, publication_id: UUID, records: Iterable[SourceCoverageRecord]
    ) -> None:
        """active immutable publication에 겹치지 않는 검증 날짜 coverage만 append한다."""

        materialized = tuple(records)
        if not materialized:
            raise ValueError("COVERAGE_RECORDS_EMPTY")
        with psycopg.connect(self._importer_dsn) as connection:
            if self._assume_role:
                connection.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._assume_role))
                )
            active = connection.execute(
                """SELECT publication.source_id
                   FROM source_admin.publication publication
                   JOIN source_admin.acquisition acquisition
                     ON acquisition.acquisition_id = publication.acquisition_id
                   JOIN source_admin.active_snapshot snapshot
                     ON snapshot.source_id = publication.source_id
                    AND snapshot.publication_id = publication.publication_id
                   WHERE publication.publication_id = %s
                     AND acquisition.status = 'PUBLISHED'""",
                (publication_id,),
            ).fetchone()
            if active is None:
                raise ValueError("COVERAGE_PUBLICATION_NOT_ACTIVE")
            source_id = str(active[0])
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source_id,)
            )
            _validate_appended_coverage(source_id, materialized)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.source_coverage(
                           publication_id, capability, coverage_ratio, reason_code,
                           region_code, grid_id, service_date_from, service_date_to,
                           blocking_reason
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        (
                            publication_id,
                            record.capability,
                            record.coverage_ratio,
                            record.blocking_reason,
                            record.region_code,
                            record.grid_id,
                            record.service_date_from,
                            record.service_date_to,
                            record.blocking_reason,
                        )
                        for record in materialized
                    ],
                )

    def _publish[T](
        self,
        acquisition_id: UUID,
        records: tuple[T, ...],
        insert_records: Callable[[psycopg.Connection[Any], _PublicationBasis], None],
    ) -> ProjectionPublication:
        if not records:
            raise ValueError("PUBLICATION_RECORDS_EMPTY")
        with psycopg.connect(self._importer_dsn) as connection:
            if self._assume_role:
                connection.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._assume_role))
                )
            acquisition = connection.execute(
                """SELECT source_id, status, raw_checksum, normalized_checksum,
                          normalization_schema_version, temporal_basis, source_date,
                          observed_at, collected_at
                   FROM source_admin.acquisition
                   WHERE acquisition_id = %s FOR UPDATE""",
                (acquisition_id,),
            ).fetchone()
            if acquisition is None:
                raise ValueError("ACQUISITION_NOT_FOUND")
            (
                source_id,
                status,
                raw_checksum,
                normalized_checksum,
                schema_version,
                temporal_basis,
                source_date,
                observed_at,
                collected_at,
            ) = acquisition
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source_id,)
            )
            if status in {"STAGED", "PUBLISHED"}:
                existing = connection.execute(
                    """SELECT publication_id, dataset_version, published_at
                       FROM source_admin.publication WHERE acquisition_id = %s""",
                    (acquisition_id,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("PUBLISHED_ACQUISITION_WITHOUT_PUBLICATION")
                return ProjectionPublication(
                    existing[0], str(source_id), str(existing[1]), existing[2], len(records), False
                )
            if status != "VALIDATED" or not normalized_checksum:
                raise ValueError("ACQUISITION_NOT_VALIDATED")
            effective_observed = observed_at or collected_at
            data_as_of = source_date or effective_observed.date()
            dataset_version = f"{data_as_of.isoformat()}-{normalized_checksum[:12]}"
            existing_publication = connection.execute(
                """SELECT publication.publication_id,
                          publication.dataset_version, publication.published_at
                   FROM source_admin.publication publication
                   WHERE publication.source_id = %s
                     AND publication.dataset_version = %s""",
                (source_id, dataset_version),
            ).fetchone()
            if existing_publication is not None:
                updated = connection.execute(
                    """UPDATE source_admin.acquisition SET status = 'NO_CHANGE'
                       WHERE acquisition_id = %s AND status = 'VALIDATED'""",
                    (acquisition_id,),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("NO_CHANGE_STATE_TRANSITION_FAILED")
                return ProjectionPublication(
                    existing_publication[0],
                    str(source_id),
                    str(existing_publication[1]),
                    existing_publication[2],
                    len(records),
                    False,
                )
            row = connection.execute(
                """INSERT INTO source_admin.publication(
                       source_id, acquisition_id, dataset_version, raw_checksum,
                       normalized_checksum, normalization_schema_version, temporal_basis,
                       source_date, observed_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING publication_id, published_at""",
                (
                    source_id,
                    acquisition_id,
                    dataset_version,
                    raw_checksum,
                    normalized_checksum,
                    schema_version,
                    temporal_basis,
                    source_date,
                    observed_at,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("PUBLICATION_INSERT_FAILED")
            publication_id, published_at = row
            insert_records(
                connection,
                _PublicationBasis(str(source_id), publication_id, data_as_of, effective_observed),
            )
            updated = connection.execute(
                """UPDATE source_admin.acquisition SET status = 'STAGED'
                   WHERE acquisition_id = %s AND status = 'VALIDATED'""",
                (acquisition_id,),
            )
            if updated.rowcount != 1:
                raise RuntimeError("PUBLICATION_STATE_TRANSITION_FAILED")
            return ProjectionPublication(
                publication_id,
                str(source_id),
                dataset_version,
                published_at,
                len(records),
                True,
            )
