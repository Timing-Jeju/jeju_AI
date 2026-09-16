"""jeju_runtime DSN으로 active read view만 조회하는 repository."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import psycopg

from jeju_trip.domain.models import (
    ActivitiesOnlyEvaluationInput,
    BusStopInspection,
    Coordinates,
    DayTripResponse,
    EvaluateJejuDayTripInput,
    FullTimelineEvaluationInput,
    InspectBusStopInput,
    PlaceReference,
    PlaceSummary,
    PreviewTransferInput,
    PreviewTransferResponse,
    RecommendDayTripsInput,
    SearchPlacesInput,
    SearchPlacesResponse,
    SourceRef,
)
from jeju_trip.domain.readiness import CapabilityReason, CapabilityState
from jeju_trip.infrastructure.source_catalog import (
    KST,
    SourceCatalog,
    SourceNotApprovedError,
    load_default_source_catalog,
)


class ActiveTravelReadRepository:
    def __init__(
        self,
        runtime_dsn: str,
        source_catalog: SourceCatalog | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime_dsn = runtime_dsn
        self._source_catalog = source_catalog or load_default_source_catalog()
        self._now = now or (lambda: datetime.now(UTC))

    def recommend(self, request: RecommendDayTripsInput) -> DayTripResponse | None:
        # 실제 scheduler는 active 장소·시간표·입구·mapping coverage가 모두 준비된 뒤 연결한다.
        return None

    def resolve_request(self, request: RecommendDayTripsInput) -> RecommendDayTripsInput:
        """active place에서 유일하게 확인된 이름 입력만 공개 place ID로 확정한다."""

        accommodation_id = self._resolve_place_id(
            place_id=request.accommodation.place_id,
            name=request.accommodation.name,
            address=request.accommodation.address,
            coordinates=request.accommodation.coordinates,
        )
        accommodation = request.accommodation.model_copy(update={"place_id": accommodation_id})

        def resolve_reference(reference: PlaceReference) -> PlaceReference:
            return reference.model_copy(
                update={
                    "place_id": self._resolve_place_id(
                        place_id=reference.place_id,
                        name=reference.name,
                        address=reference.address,
                        coordinates=reference.coordinates,
                    )
                }
            )

        day_boundary = (
            request.day_boundary.model_copy(
                update={
                    "start_place": resolve_reference(request.day_boundary.start_place),
                    "end_place": resolve_reference(request.day_boundary.end_place),
                }
            )
            if request.day_boundary is not None
            else None
        )
        return request.model_copy(
            update={
                "accommodation": accommodation,
                "day_boundary": day_boundary,
                "required_places": tuple(
                    resolve_reference(item) for item in request.required_places
                ),
                "preferred_places": tuple(
                    resolve_reference(item) for item in request.preferred_places
                ),
                "excluded_places": tuple(
                    resolve_reference(item) for item in request.excluded_places
                ),
            }
        )

    def _resolve_place_id(
        self,
        *,
        place_id: str | None,
        name: str | None,
        address: str | None,
        coordinates: Coordinates | None,
    ) -> str | None:
        """ID는 존재 여부를, 이름 입력은 exact match의 유일성을 검증한다."""

        clauses = []
        parameters: dict[str, object] = {}
        if place_id is not None:
            clauses.append("fact_id = %(place_id)s")
            parameters["place_id"] = place_id
        else:
            clauses.append("name = %(name)s")
            parameters["name"] = name
            if address is not None:
                clauses.append("address = %(address)s")
                parameters["address"] = address
            if coordinates is not None:
                clauses.append(
                    "ST_DWithin(position, "
                    "ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)::geography, "
                    "50)"
                )
                parameters["longitude"] = coordinates.longitude
                parameters["latitude"] = coordinates.latitude
        statement = (
            "SELECT DISTINCT fact_id FROM travel_read.active_place WHERE "
            + " AND ".join(clauses)
            + " LIMIT 2"
        )
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return str(rows[0][0]) if len(rows) == 1 else None

    def places_covered_by_jeju(self, place_ids: tuple[str, ...]) -> bool:
        """모든 확정 장소가 active 제주 경계의 ST_Covers 판정을 통과하는지 확인한다."""

        statement = """
        SELECT bool_and(EXISTS (
          SELECT 1
          FROM travel_read.active_place place
          CROSS JOIN travel_read.active_service_area_boundary boundary
          WHERE place.fact_id = requested.place_id
            AND ST_Covers(boundary.geometry, place.position::geometry)
        ))
        FROM unnest(%(place_ids)s::text[]) AS requested(place_id)
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(statement, {"place_ids": list(place_ids)}).fetchone()
        return bool(row and row[0])

    def places_in_scope(self, place_ids: tuple[str, ...], region_code: str, grid_id: str) -> bool:
        """모든 요청 장소가 exact active scope manifest의 PLACE 구성원인지 확인한다."""

        statement = """
        SELECT bool_and(EXISTS (
          SELECT 1
          FROM travel_read.active_service_scope_member member
          WHERE member.region_code = %(region_code)s
            AND member.grid_id = %(grid_id)s
            AND member.member_type = 'PLACE'
            AND member.member_id = requested.place_id
        ))
        FROM unnest(%(place_ids)s::text[]) AS requested(place_id)
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                statement,
                {
                    "place_ids": list(place_ids),
                    "region_code": region_code,
                    "grid_id": grid_id,
                },
            ).fetchone()
        return bool(row and row[0])

    def search_places(self, request: SearchPlacesInput) -> SearchPlacesResponse:
        states = self.capability_states(self._now().astimezone(KST).date())
        required_states = tuple(
            states.get(
                capability,
                CapabilityState.unavailable(CapabilityReason.MISSING),
            )
            for capability in ("place_search_ready", "service_area_ready")
        )
        if any(not state.ready for state in required_states):
            reason = (
                "PLACE_SEARCH_SOURCE_STALE"
                if any(state.reason == CapabilityReason.STALE for state in required_states)
                else "PLACE_SEARCH_DATA_NOT_READY"
            )
            return SearchPlacesResponse(status="data_unavailable", reason_code=reason)
        freshness_days = self._source_catalog.require(
            "tourapi.place"
        ).temporal.freshness_days
        statement = """
        SELECT place.fact_id, place.name,
               COALESCE(place.attributes->>'category_level_3', place.category) AS category,
               place.address,
               ST_Y(place.position::geometry) AS latitude,
               ST_X(place.position::geometry) AS longitude,
               place.source_id,
               place.publication_id::text,
               boundary.fact_id,
               boundary_metadata.source_id,
               boundary.publication_id::text,
               (SELECT count(*) FROM travel_read.active_place_entrance entrance
                WHERE entrance.place_fact_id = place.fact_id
                  AND entrance.verification_status = 'VERIFIED'
                  AND ST_Covers(boundary.geometry, entrance.position::geometry)
               ) AS entrance_count
        FROM travel_read.active_place place
        CROSS JOIN travel_read.active_service_area_boundary boundary
        JOIN travel_read.active_source_metadata boundary_metadata
          ON boundary_metadata.publication_id = boundary.publication_id
        WHERE place.observed_at >= now() - make_interval(days =>
            CASE WHEN place.source_id = 'kac.airport' THEN %(airport_freshness_days)s
                 ELSE %(freshness_days)s END)
          AND place.observed_at <= now() + interval '5 minutes'
          AND ST_Covers(boundary.geometry, place.position::geometry)
          AND (place.name ILIKE %(query)s OR place.address ILIKE %(query)s)
        ORDER BY place.name LIMIT %(limit)s
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                statement,
                {
                    "query": f"%{request.query}%",
                    "limit": request.limit,
                    "freshness_days": freshness_days,
                    "airport_freshness_days": self._source_catalog.require(
                        "kac.airport"
                    ).temporal.freshness_days,
                },
            ).fetchall()
        places = tuple(
            PlaceSummary(
                place_id=fact_id,
                name=name,
                category=category,
                address=address,
                position=Coordinates(latitude=latitude, longitude=longitude),
                verified_entrance_count=entrance_count,
                source_refs=(
                    SourceRef(
                        source_id=place_source_id,
                        publication_id=publication_id,
                        source_fact_id=fact_id,
                    ),
                    SourceRef(
                        source_id=boundary_source_id,
                        publication_id=boundary_publication_id,
                        source_fact_id=boundary_fact_id,
                    ),
                ),
            )
            for (
                fact_id,
                name,
                category,
                address,
                latitude,
                longitude,
                place_source_id,
                publication_id,
                boundary_fact_id,
                boundary_source_id,
                boundary_publication_id,
                entrance_count,
            ) in rows
        )
        return SearchPlacesResponse(status="success", places=places)

    def inspect_bus_stop(self, request: InspectBusStopInput) -> BusStopInspection:
        source_states = tuple(
            self.source_state(source_id)
            for source_id in ("tago.bus-stop", "transport.stop-identity-map")
        )
        if any(not state.ready for state in source_states):
            reason = (
                "BUS_STOP_SOURCE_STALE"
                if any(state.reason == CapabilityReason.STALE for state in source_states)
                else "BUS_STOP_DATA_NOT_READY"
            )
            return BusStopInspection(status="data_unavailable", reason_code=reason)
        statement = """
        SELECT identity.canonical_stop_id, stop.provider_stop_id, stop.name,
               COALESCE(identity.direction_text, stop.direction_text) AS direction_text,
               ST_Y(stop.position::geometry) AS latitude,
               ST_X(stop.position::geometry) AS longitude,
               identity.mapping_method, identity.mapping_status,
               COALESCE((
                 SELECT array_agg(DISTINCT route.route_number ORDER BY route.route_number)
                 FROM travel_read.active_stop_time stop_time
                 JOIN travel_read.active_scheduled_trip trip
                   ON trip.trip_id = stop_time.trip_id
                 JOIN travel_read.active_bus_route route
                   ON route.fact_id = trip.route_fact_id
                 WHERE stop_time.stop_fact_id = stop.fact_id
               ), ARRAY[]::text[]) AS route_numbers,
               stop.source_id, stop.publication_id::text, stop.fact_id,
               identity_metadata.source_id,
               identity.publication_id::text, identity.fact_id
        FROM travel_read.active_bus_stop stop
        JOIN travel_read.active_stop_identity identity ON identity.source_fact_id = stop.fact_id
        JOIN travel_read.active_source_metadata identity_metadata
          ON identity_metadata.publication_id = identity.publication_id
        WHERE identity.canonical_stop_id = %(stop_id)s OR stop.provider_stop_id = %(stop_id)s
        LIMIT 1
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(statement, {"stop_id": request.stop_id}).fetchone()
        if row is None:
            return BusStopInspection(status="not_found", reason_code="BUS_STOP_NOT_FOUND")
        (
            canonical_stop_id,
            provider_stop_id,
            name,
            direction_text,
            latitude,
            longitude,
            mapping_method,
            mapping_status,
            route_numbers,
            stop_source_id,
            stop_publication_id,
            stop_fact_id,
            identity_source_id,
            identity_publication_id,
            identity_fact_id,
        ) = row
        return BusStopInspection(
            status="success",
            canonical_stop_id=canonical_stop_id,
            provider_stop_id=provider_stop_id,
            name=name,
            direction_text=direction_text,
            position=Coordinates(latitude=latitude, longitude=longitude),
            mapping_method=mapping_method,
            mapping_status=mapping_status,
            route_numbers=tuple(str(value) for value in route_numbers),
            source_refs=(
                SourceRef(
                    source_id=stop_source_id,
                    publication_id=stop_publication_id,
                    source_fact_id=stop_fact_id,
                ),
                SourceRef(
                    source_id=identity_source_id,
                    publication_id=identity_publication_id,
                    source_fact_id=identity_fact_id,
                ),
            ),
        )

    def preview_transfer(self, request: PreviewTransferInput) -> PreviewTransferResponse:
        return PreviewTransferResponse(
            status="unavailable", reason_code="TMAP_ADAPTER_NOT_CONFIGURED"
        )

    def resolve_stop(self, canonical_stop_id: str) -> tuple[str, str, str] | None:
        """실시간 TAGO 조회에 필요한 confirmed 정류장 ID와 city code만 반환한다."""

        statement = """
        SELECT stop.attributes->>'city_code', stop.provider_stop_id, identity.mapping_status
        FROM travel_read.active_stop_identity identity
        JOIN travel_read.active_bus_stop stop ON stop.fact_id = identity.source_fact_id
        WHERE identity.canonical_stop_id = %(stop_id)s
        LIMIT 1
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(statement, {"stop_id": canonical_stop_id}).fetchone()
        if row is None or not row[0]:
            return None
        return str(row[0]), str(row[1]), str(row[2])

    def capability_flags(
        self, trip_date, region_code: str = "JEJU_ALL", grid_id: str = "ALL"
    ) -> dict[str, bool]:
        """전역 경계와 요청 scope의 날짜별 atomic coverage를 결합한다."""

        statement = """
        SELECT coverage.capability,
               coverage.coverage_ratio = 1 AND coverage.blocking_reason IS NULL AS ready,
               metadata.source_id, metadata.source_date, metadata.observed_at,
               COALESCE(metadata.observed_at, metadata.published_at) AS retrieved_at
        FROM travel_read.active_source_coverage coverage
        JOIN travel_read.active_source_metadata metadata
          ON metadata.publication_id = coverage.publication_id
        WHERE (coverage.service_date_from IS NULL
               OR coverage.service_date_from <= %(trip_date)s)
          AND (coverage.service_date_to IS NULL
               OR coverage.service_date_to >= %(trip_date)s)
          AND (
            (coverage.capability IN ('service_area_ready', 'place_search_ready')
             AND coverage.region_code = 'JEJU_ALL' AND coverage.grid_id = 'ALL')
            OR
            (coverage.capability NOT IN ('service_area_ready', 'place_search_ready')
             AND coverage.region_code = %(region_code)s
             AND coverage.grid_id = %(grid_id)s)
          )
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                statement,
                {"trip_date": trip_date, "region_code": region_code, "grid_id": grid_id},
            ).fetchall()
        flags: dict[str, bool] = {}
        for capability, ready, source_id, source_date, observed_at, retrieved_at in rows:
            try:
                source = self._source_catalog.require(str(source_id))
            except SourceNotApprovedError:
                fresh = False
            else:
                fresh = source.is_fresh(
                    source_date=source_date,
                    observed_at=observed_at,
                    retrieved_at=retrieved_at,
                    now=self._now(),
                )
            capability_name = str(capability)
            flags[capability_name] = flags.get(capability_name, True) and bool(ready) and fresh
        return flags

    def capability_states(
        self, trip_date, region_code: str = "JEJU_ALL", grid_id: str = "ALL"
    ) -> dict[str, CapabilityState]:
        """active coverage의 범위·차단·freshness 원인을 잃지 않고 집계한다."""

        checked_date = (
            trip_date
            if not isinstance(trip_date, str)
            else datetime.fromisoformat(trip_date).date()
        )
        statement = """
        SELECT coverage.capability, coverage.coverage_ratio, coverage.blocking_reason,
               coverage.region_code, coverage.grid_id,
               coverage.service_date_from, coverage.service_date_to,
               metadata.source_id, metadata.source_date, metadata.observed_at,
               COALESCE(metadata.observed_at, metadata.published_at) AS retrieved_at
        FROM travel_read.active_source_coverage coverage
        JOIN travel_read.active_source_metadata metadata
          ON metadata.publication_id = coverage.publication_id
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(statement).fetchall()

        grouped: dict[str, list[tuple[bool, tuple]]] = {}
        for row in rows:
            capability, _, _, row_region, row_grid, starts_on, ends_on, source_id, *_ = row
            capability_name = self._normalized_capability(str(capability), str(source_id))
            expected_region = (
                "JEJU_ALL"
                if capability_name in {"service_area_ready", "place_search_ready"}
                else region_code
            )
            expected_grid = (
                "ALL"
                if capability_name in {"service_area_ready", "place_search_ready"}
                else grid_id
            )
            scope_matches = str(row_region) == expected_region and str(row_grid) == expected_grid
            date_matches = (starts_on is None or starts_on <= checked_date) and (
                ends_on is None or ends_on >= checked_date
            )
            grouped.setdefault(capability_name, []).append(
                (scope_matches and date_matches, row)
            )

        states: dict[str, CapabilityState] = {}
        for capability, candidates in grouped.items():
            relevant = [row for matches, row in candidates if matches]
            considered = relevant or [row for _, row in candidates]
            source_failure = self._source_failure_reason(
                [tuple(row[7:11]) for row in considered]
            )
            if source_failure is not None:
                states[capability] = CapabilityState.unavailable(source_failure)
                continue
            if not relevant:
                states[capability] = CapabilityState.unavailable(
                    CapabilityReason.COVERAGE_INCOMPLETE
                )
                continue
            if any(float(row[1]) < 1 for row in relevant) or any(row[2] for row in relevant):
                blocking_reasons = {str(row[2]) for row in relevant if row[2]}
                reason = (
                    CapabilityReason.COVERAGE_INCOMPLETE
                    if not blocking_reasons
                    or blocking_reasons == {CapabilityReason.COVERAGE_INCOMPLETE.value}
                    else CapabilityReason.BLOCKED
                )
                states[capability] = CapabilityState.unavailable(reason)
                continue
            states[capability] = CapabilityState.available()
        return states

    def source_state(self, source_id: str) -> CapabilityState:
        """직접 조회 도구가 사용하는 active source 자체의 freshness를 판정한다."""

        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                """SELECT source_id, source_date, observed_at,
                          COALESCE(observed_at, published_at) AS retrieved_at
                   FROM travel_read.active_source_metadata
                   WHERE source_id = %s""",
                (source_id,),
            ).fetchall()
        if not rows:
            return CapabilityState.unavailable(CapabilityReason.MISSING)
        failure = self._source_failure_reason([tuple(row) for row in rows])
        return (
            CapabilityState.unavailable(failure)
            if failure is not None
            else CapabilityState.available()
        )

    @staticmethod
    def _normalized_capability(capability: str, source_id: str) -> str:
        if capability != "fare_policy_ready":
            return capability
        if source_id == "jeju.bus-fare-policy":
            return "bus_fare_policy_ready"
        if source_id == "jeju.taxi-fare-policy":
            return "taxi_fare_policy_ready"
        return capability

    def _source_failure_reason(self, rows: list[tuple]) -> CapabilityReason | None:
        for source_id, source_date, observed_at, retrieved_at in rows:
            try:
                source = self._source_catalog.require(str(source_id))
            except SourceNotApprovedError:
                return CapabilityReason.SOURCE_NOT_APPROVED
            if not source.is_fresh(
                source_date=source_date,
                observed_at=observed_at,
                retrieved_at=retrieved_at,
                now=self._now(),
            ):
                return CapabilityReason.STALE
        return None

    def exact_bus_planning_available(self, request: RecommendDayTripsInput) -> bool:
        """전역 coverage가 미완료여도 요청 장소 주변 exact service-day 존재를 확인한다."""

        place_ids = tuple(
            dict.fromkeys(
                place_id
                for place_id in (
                    request.accommodation.place_id,
                    *(item.place_id for item in request.required_places),
                    *(item.place_id for item in request.preferred_places),
                )
                if place_id is not None
            )
        )
        if not place_ids:
            return False
        return self._exact_bus_service_available(place_ids, request.trip_date)

    def exact_bus_evaluation_available(self, request: EvaluateJejuDayTripInput) -> bool:
        """판정 일정의 버스 endpoint마다 exact service-day 정류장 근거가 있는지 확인한다."""

        if isinstance(request, FullTimelineEvaluationInput):
            bus_transfers = tuple(
                item
                for item in request.timeline
                if item.type == "transfer" and item.planned_mode == "bus"
            )
            if not bus_transfers:
                return True
            endpoint_values = tuple(
                place_id
                for transfer in bus_transfers
                for place_id in (
                    transfer.from_place.place_id,
                    transfer.to_place.place_id,
                )
            )
        elif isinstance(request, ActivitiesOnlyEvaluationInput):
            endpoint_values = (
                request.accommodation.place_id,
                *(item.place.place_id for item in request.scheduled_activities),
                *((request.start_location.place_id,) if request.start_location else ()),
            )
        else:  # pragma: no cover - Pydantic discriminator가 두 공개 모델만 허용한다.
            return False
        if any(place_id is None for place_id in endpoint_values):
            return False
        place_ids = tuple(
            dict.fromkeys(str(place_id) for place_id in endpoint_values if place_id is not None)
        )
        return bool(place_ids) and self._exact_bus_service_available(
            place_ids, request.trip_date
        )

    def _exact_bus_service_available(self, place_ids: tuple[str, ...], trip_date) -> bool:
        """지정한 장소 모두에 해당 운행일의 confirmed 정류장이 근접하는지 조회한다."""

        day_type = (
            "SATURDAY"
            if trip_date.isoweekday() == 6
            else "SUNDAY"
            if trip_date.isoweekday() == 7
            else "WEEKDAY"
        )
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """WITH endpoint AS MATERIALIZED (
                     SELECT place.fact_id, place.position
                     FROM travel_read.active_place place
                     WHERE place.fact_id = ANY(%(place_ids)s::text[])
                   ), eligible_stop AS MATERIALIZED (
                     SELECT DISTINCT stop.position
                     FROM travel_read.active_scheduled_trip trip
                     JOIN travel_read.active_service_calendar calendar
                       ON calendar.service_id = trip.service_id
                     JOIN travel_read.active_stop_time stop_time
                       ON stop_time.trip_id = trip.trip_id
                     JOIN travel_read.active_bus_stop stop
                       ON stop.fact_id = stop_time.stop_fact_id
                     JOIN travel_read.active_stop_identity identity
                       ON identity.source_fact_id = stop.fact_id
                      AND identity.mapping_status = 'CONFIRMED'
                     WHERE calendar.starts_on <= %(trip_date)s
                       AND calendar.ends_on >= %(trip_date)s
                       AND calendar.day_type = CASE
                         WHEN EXISTS (
                           SELECT 1 FROM travel_read.active_holiday holiday
                           WHERE holiday.holiday_date = %(trip_date)s
                             AND holiday.is_public_institution_holiday
                         ) THEN 'HOLIDAY'
                         ELSE %(day_type)s
                       END
                       AND (trip.timetable_effective_from IS NULL
                            OR trip.timetable_effective_from <= %(trip_date)s)
                       AND (trip.timetable_effective_to IS NULL
                            OR trip.timetable_effective_to >= %(trip_date)s)
                       AND NOT EXISTS (
                         SELECT 1
                         FROM travel_read.active_service_calendar_exception exception
                         WHERE exception.service_id = trip.service_id
                           AND exception.exception_date = %(trip_date)s
                           AND exception.exception_type = 'REMOVED'
                       )
                   )
                   SELECT count(*) = cardinality(%(place_ids)s::text[])
                          AND bool_and(EXISTS (
                            SELECT 1 FROM eligible_stop stop
                            WHERE ST_DWithin(stop.position, endpoint.position, 2500)
                          ))
                   FROM endpoint""",
                {
                    "place_ids": list(place_ids),
                    "trip_date": trip_date,
                    "day_type": day_type,
                },
            ).fetchone()
        return bool(row and row[0])
