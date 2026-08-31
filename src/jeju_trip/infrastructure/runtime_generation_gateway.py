"""active PostGIS publication과 일시적 TMAP 경로를 생성 엔진에 연결한다."""

from __future__ import annotations

import itertools
import math
from bisect import bisect_left
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, LiteralString, cast
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql

from jeju_trip.domain.models import (
    AccommodationInput,
    Coordinates,
    DataSourceMetadata,
    Derivation,
    EndpointBasis,
    EvidenceFact,
    PlaceReference,
    RecommendDayTripsInput,
    SourceRef,
    Strategy,
)
from jeju_trip.infrastructure.runtime_routing import (
    TmapDoorRoutePlanner,
    VerifiedEntrance,
)
from jeju_trip.infrastructure.source_catalog import (
    SourceCatalog,
    SourceNotApprovedError,
    load_default_source_catalog,
)
from jeju_trip.planning.execution_budget import ExecutionBudget
from jeju_trip.planning.generation import (
    VerifiedGenerationPlace,
    VerifiedOpeningWindow,
    VerifiedRouteOption,
    hard_rest_limit_required,
    strategy_transfer_slack_minutes,
)
from jeju_trip.planning.multi_day import history_place_policy
from jeju_trip.planning.policy import PlanningPolicy

KST = ZoneInfo("Asia/Seoul")


def _subtract_verified_breaks(
    windows: Sequence[VerifiedOpeningWindow],
    break_rows: Sequence[Sequence[Any]],
    day_start: datetime,
) -> tuple[VerifiedOpeningWindow, ...]:
    """검증 BREAK 구간을 OPEN 구간에서 빼고 경계 근거 fact를 함께 보존한다."""

    remaining = list(windows)
    for row in break_rows:
        break_start = day_start + timedelta(minutes=int(row[2]))
        break_end = day_start + timedelta(days=int(row[4]), minutes=int(row[3]))
        break_fact_id = str(row[0])
        next_windows: list[VerifiedOpeningWindow] = []
        for window in remaining:
            if break_end <= window.opens_at or break_start >= window.closes_at:
                next_windows.append(window)
                continue
            evidence_fact_ids = tuple(
                dict.fromkeys((*window.evidence_fact_ids, break_fact_id))
            )
            if window.opens_at < break_start:
                next_windows.append(
                    replace(
                        window,
                        closes_at=break_start,
                        evidence_fact_ids=evidence_fact_ids,
                    )
                )
            if break_end < window.closes_at:
                next_windows.append(
                    replace(
                        window,
                        opens_at=break_end,
                        evidence_fact_ids=evidence_fact_ids,
                    )
                )
        remaining = next_windows
    return tuple(
        sorted(
            remaining,
            key=lambda item: (item.opens_at, item.closes_at, item.evidence_fact_ids),
        )
    )


def _verified_opening_windows(
    rows: Sequence[Sequence[Any]], day_start: datetime
) -> tuple[VerifiedOpeningWindow, ...]:
    """동일 날짜의 검증 OPEN/BREAK 행을 실제 체류 가능한 구간으로 변환한다."""

    open_rows = tuple(row for row in rows if row[11] == "OPEN")
    break_rows = tuple(row for row in rows if row[11] == "BREAK")
    windows = tuple(
        VerifiedOpeningWindow(
            opens_at=day_start + timedelta(minutes=int(row[2])),
            closes_at=day_start
            + timedelta(days=int(row[4]), minutes=int(row[3])),
            last_admission_at=(
                day_start + timedelta(minutes=int(row[5]))
                if row[5] is not None
                else None
            ),
            last_order_at=(
                day_start + timedelta(minutes=int(row[6]))
                if row[6] is not None
                else None
            ),
            evidence_fact_ids=(str(row[0]),),
        )
        for row in open_rows
    )
    return _subtract_verified_breaks(windows, break_rows, day_start)


@dataclass(frozen=True)
class _BusCandidateLeg:
    origin_id: str
    destination_id: str
    scheduled_departure_at: datetime
    scheduled_arrival_at: datetime
    access_distance_meters: float
    egress_distance_meters: float
    transfers: int


@dataclass(frozen=True)
class _ScheduledStopCall:
    trip_fact_id: str
    route_number: str
    stop_fact_id: str
    stop_sequence: int
    arrival_at: datetime
    departure_at: datetime
    latitude: float
    longitude: float


class PostgresGenerationGateway:
    """요청마다 새로 만들어 evidence ledger가 사용자 요청 사이에 섞이지 않게 한다."""

    def __init__(
        self,
        runtime_dsn: str,
        route_planner: TmapDoorRoutePlanner,
        planning_policy: PlanningPolicy,
        source_catalog: SourceCatalog | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime_dsn = runtime_dsn
        self._route_planner = route_planner
        self._planning_policy = planning_policy
        self._source_catalog = source_catalog or load_default_source_catalog()
        self._now = now or (lambda: datetime.now(UTC))
        self._facts: dict[str, EvidenceFact] = {}
        self._sources: dict[str, DataSourceMetadata] = {}
        self._entrances: dict[str, tuple[VerifiedEntrance, ...]] = {}
        self._place_rows: dict[str, tuple[Any, ...] | None] = {}
        self._opening_rows: dict[str, tuple[tuple[Any, ...], ...]] = {}
        self._exception_rows: dict[str, tuple[Any, ...] | None] = {}
        self._entrance_rows: dict[str, tuple[tuple[Any, ...], ...]] = {}
        self._place_freshness: dict[str, bool] = {}

    def resolve_request(self, request: RecommendDayTripsInput) -> RecommendDayTripsInput:
        accommodation = self._resolve_accommodation(request.accommodation)
        day_boundary = (
            request.day_boundary.model_copy(
                update={
                    "start_place": self._resolve_reference(
                        request.day_boundary.start_place
                    ),
                    "end_place": self._resolve_reference(request.day_boundary.end_place),
                }
            )
            if request.day_boundary is not None
            else None
        )
        required = tuple(self._resolve_reference(item) for item in request.required_places)
        preferred = tuple(self._resolve_reference(item) for item in request.preferred_places)
        excluded = tuple(self._resolve_reference(item) for item in request.excluded_places)
        return request.model_copy(
            update={
                "accommodation": accommodation,
                "day_boundary": day_boundary,
                "required_places": required,
                "preferred_places": preferred,
                "excluded_places": excluded,
            }
        )

    def register_current_endpoint(
        self,
        place_id: str,
        position: Coordinates,
        allowed_modes: set[Literal["walk", "bus", "taxi"]],
    ) -> None:
        """현재 GPS를 요청 수명 동안만 경로 endpoint로 보관한다."""

        self._entrances[place_id] = (
            VerifiedEntrance(
                entrance_id=f"current-gps:{place_id}",
                place_id=place_id,
                position=position,
                supported_modes=tuple(sorted(allowed_modes)),
                endpoint_basis=EndpointBasis.CURRENT_GPS,
            ),
        )

    def _resolve_accommodation(self, item: AccommodationInput) -> AccommodationInput:
        resolved = self._resolve_reference(
            PlaceReference(
                place_id=item.place_id,
                name=item.name,
                address=item.address,
                coordinates=item.coordinates,
            )
        )
        return AccommodationInput(
            place_id=resolved.place_id,
            name=resolved.name or item.name,
            address=resolved.address,
            coordinates=resolved.coordinates,
        )

    def _resolve_reference(self, item: PlaceReference) -> PlaceReference:
        clauses: list[str] = []
        parameters: dict[str, Any] = {}
        if item.coordinates is not None:
            self._assert_position_in_service_area(
                item.coordinates.latitude, item.coordinates.longitude
            )
        if item.place_id:
            clauses.append("place.fact_id = %(place_id)s")
            parameters["place_id"] = item.place_id
        elif item.name:
            clauses.append("lower(trim(place.name)) = lower(trim(%(name)s))")
            parameters["name"] = item.name
            if item.address:
                clauses.append("place.address ILIKE %(address)s")
                parameters["address"] = f"%{item.address}%"
        else:
            raise ValueError("PLACE_UNRESOLVED")
        distance_sql = "NULL::double precision"
        if item.coordinates is not None:
            distance_sql = (
                "ST_Distance(place.position, ST_SetSRID(ST_MakePoint(%(longitude)s, "
                "%(latitude)s), 4326)::geography)"
            )
            parameters.update(
                {
                    "latitude": item.coordinates.latitude,
                    "longitude": item.coordinates.longitude,
                }
            )
        statement = f"""
        SELECT place.fact_id, place.name, place.address,
               ST_Y(place.position::geometry), ST_X(place.position::geometry),
               {distance_sql} AS distance, place.source_id, place.data_as_of,
               place.observed_at
        FROM travel_read.active_place place
        CROSS JOIN travel_read.active_service_area_boundary boundary
        WHERE {" AND ".join(clauses)}
          AND ST_Covers(boundary.geometry, place.position::geometry)
        ORDER BY distance NULLS LAST, place.fact_id
        LIMIT 3
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                sql.SQL(cast(LiteralString, statement)), parameters
            ).fetchall()
        rows = [
            row
            for row in rows
            if self._source_is_fresh(
                str(row[6]), source_date=row[7], observed_at=row[8]
            )
        ]
        if not rows:
            raise ValueError("PLACE_UNRESOLVED")
        if len(rows) > 1:
            first_distance = rows[0][5]
            second_distance = rows[1][5]
            coordinate_disambiguated = (
                first_distance is not None
                and second_distance is not None
                and first_distance <= 100
                and second_distance - first_distance >= 50
            )
            if not coordinate_disambiguated:
                raise ValueError("PLACE_AMBIGUOUS")
        row = rows[0]
        self._record_active_boundary_fact()
        return PlaceReference(
            place_id=str(row[0]),
            name=str(row[1]),
            address=str(row[2]),
            coordinates=Coordinates(latitude=float(row[3]), longitude=float(row[4])),
        )

    def places(self, request: RecommendDayTripsInput) -> tuple[VerifiedGenerationPlace, ...]:
        requested_ids = tuple(
            dict.fromkeys(
                item.place_id
                for item in (*request.required_places, *request.preferred_places)
                if item.place_id is not None
            )
        )
        clustered_ids = self._clustered_candidate_ids(request)
        candidate_ids = tuple(dict.fromkeys((*requested_ids, *clustered_ids)))
        boundary_ids = tuple(
            dict.fromkeys(
                item.place_id
                for item in (request.start_boundary, request.end_boundary)
                if item.place_id is not None
            )
        )
        self._prime_place_candidates(
            tuple(
                dict.fromkeys(
                    (*candidate_ids, *boundary_ids)
                )
            ),
            request,
        )
        places: list[VerifiedGenerationPlace] = []
        dietary_constraints = bool(request.food.allergens or request.food.excluded_foods)
        for place_id in candidate_ids:
            place = self._load_place(place_id, request)
            if place is None:
                continue
            if dietary_constraints and place.activity_type == "meal":
                dietary = self.dietary_safety(
                    place.place_id,
                    request.food.allergens,
                    request.food.excluded_foods,
                    request.trip_date,
                )
                if dietary is None or not dietary[0]:
                    continue
                place = replace(
                    place,
                    evidence_fact_ids=tuple(
                        dict.fromkeys((*place.evidence_fact_ids, *dietary[1]))
                    ),
                )
            places.append(place)
        for boundary_id in boundary_ids:
            self._entrances[boundary_id] = self._load_entrances(boundary_id, request)
        return tuple(places)

    def dietary_safety(
        self,
        place_id: str,
        allergens: tuple[str, ...],
        excluded_foods: tuple[str, ...],
        on_date: date,
    ) -> tuple[bool, tuple[str, ...]] | None:
        """요청 문자열을 추정 확장하지 않고 exact 검증 부재 목록을 모두 덮는 메뉴를 찾는다."""

        normalized_allergens = tuple(
            dict.fromkeys(value.strip().casefold() for value in allergens if value.strip())
        )
        normalized_excluded = tuple(
            dict.fromkeys(value.strip().casefold() for value in excluded_foods if value.strip())
        )
        if not normalized_allergens and not normalized_excluded:
            return True, ()
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """SELECT dietary.fact_id, dietary.menu_item_id, dietary.menu_item_name,
                          dietary.verified_free_from_allergens,
                          dietary.verified_excludes_foods,
                          dietary.publication_id::text, metadata.source_id,
                          metadata.source_date, metadata.observed_at
                   FROM travel_read.active_restaurant_dietary_fact dietary
                   JOIN travel_read.active_source_metadata metadata
                     ON metadata.publication_id = dietary.publication_id
                   WHERE dietary.place_fact_id = %s
                     AND dietary.verified_free_from_allergens @> %s::text[]
                     AND dietary.verified_excludes_foods @> %s::text[]
                     AND dietary.valid_from <= %s
                     AND dietary.valid_to >= %s
                     AND dietary.verification_expires_at >= (%s::date + INTERVAL '1 day')
                   ORDER BY dietary.menu_item_id, dietary.fact_id
                   LIMIT 1""",
                (
                    place_id,
                    list(normalized_allergens),
                    list(normalized_excluded),
                    on_date,
                    on_date,
                    on_date,
                ),
            ).fetchone()
        if row is None or not self._source_is_fresh(
            str(row[6]), source_date=row[7], observed_at=row[8]
        ):
            return None
        fact_id = str(row[0])
        self._add_source_fact(
            fact_id,
            "restaurant_dietary_safety",
            {
                "menu_item_id": str(row[1]),
                "menu_item_name": str(row[2]),
                "verified_free_from_allergens": tuple(str(value) for value in row[3]),
                "verified_excludes_foods": tuple(str(value) for value in row[4]),
            },
            str(row[6]),
            str(row[5]),
            row[7] or row[8].date(),
            row[8] or datetime.now(UTC),
        )
        return True, (fact_id,)

    def bus_only_candidate_orders(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[tuple[str, ...], ...]]:
        """exact 직통 stop-time 그래프에서 전략별 primary와 fallback을 고른다."""

        legs = self._bus_candidate_legs(request, places)
        place_by_id = {place.place_id: place for place in places}
        solutions = {
            strategy: self._bus_only_strategy_solutions(
                request,
                strategy,
                place_by_id,
                legs,
            )
            for strategy in Strategy
        }
        if any(not solutions[strategy] for strategy in Strategy):
            return {strategy: () for strategy in Strategy}

        start_place_id = request.start_boundary.place_id
        end_place_id = request.end_boundary.place_id
        assert start_place_id is not None
        assert end_place_id is not None

        def edges(order: tuple[str, ...]) -> set[tuple[str, str]]:
            return set(
                zip((start_place_id, *order), (*order, end_place_id), strict=True)
            )

        def unknown_opening_count(order: tuple[str, ...]) -> int:
            return sum(
                place_by_id[place_id].operating_hours_status != "VERIFIED"
                for place_id in order
            )

        def maximum_hotel_radius_meters(order: tuple[str, ...]) -> float:
            start_boundary = request.start_boundary.coordinates
            if start_boundary is None:
                return math.inf
            hotel_position = (start_boundary.latitude, start_boundary.longitude)
            return max(
                (
                    self._coordinate_distance_meters(
                        hotel_position,
                        (
                            place_by_id[place_id].position.latitude,
                            place_by_id[place_id].position.longitude,
                        ),
                    )
                    for place_id in order
                ),
                default=0.0,
            )

        def meal_position(order: tuple[str, ...]) -> int:
            return next(
                (
                    index
                    for index, place_id in enumerate(order)
                    if place_by_id[place_id].activity_type == "meal"
                ),
                len(order),
            )

        combinations = (
            (balanced, relaxed, experience)
            for balanced, relaxed, experience in itertools.product(
                solutions[Strategy.BALANCED],
                solutions[Strategy.RELAXED],
                solutions[Strategy.EXPERIENCE_MAX],
            )
            if self._orders_are_materially_different(balanced[1], relaxed[1])
            and (
                balanced[1] == experience[1]
                or self._orders_are_materially_different(balanced[1], experience[1])
            )
            and self._orders_are_materially_different(relaxed[1], experience[1])
        )
        chosen = min(
            combinations,
            key=lambda items: (
                sum(unknown_opening_count(item[1]) for item in items),
                max(meal_position(item[1]) for item in items),
                sum(meal_position(item[1]) for item in items),
                len(set().union(*(edges(item[1]) for item in items))),
                max(item[0] for item in items),
                tuple(item[0] for item in items),
                tuple(item[1] for item in items),
            ),
            default=None,
        )
        if chosen is None:
            return {strategy: () for strategy in Strategy}
        selected: dict[Strategy, list[tuple[str, ...]]] = {
            Strategy.BALANCED: [chosen[0][1]],
            Strategy.RELAXED: [
                self._relaxed_prefix_order(
                    chosen[0][1],
                    place_by_id,
                    {
                        item.place_id
                        for item in request.required_places
                        if item.place_id is not None
                    },
                )
            ],
            Strategy.EXPERIENCE_MAX: [chosen[2][1]],
        }

        for strategy in Strategy:
            primary = selected[strategy][0]
            primary_edges = edges(primary)
            fallback = next(
                (
                    order
                    for _, order in sorted(
                        solutions[strategy],
                        key=lambda item: (
                            unknown_opening_count(item[1]),
                            meal_position(item[1]),
                            len(edges(item[1]) & primary_edges),
                            item[0],
                            maximum_hotel_radius_meters(item[1]),
                            item[1],
                        ),
                    )
                    if order != primary
                ),
                None,
            )
            if fallback is not None:
                selected[strategy].append(fallback)
        return {strategy: tuple(orders) for strategy, orders in selected.items()}

    @staticmethod
    def _relaxed_prefix_order(
        balanced_order: tuple[str, ...],
        place_by_id: dict[str, VerifiedGenerationPlace],
        required_ids: set[str],
    ) -> tuple[str, ...]:
        """balanced prefix의 관광 2개를 보존해 버스 보행 검증을 재사용한다."""

        required_visits = {
            place_id
            for place_id in required_ids
            if place_id in place_by_id
            and place_by_id[place_id].activity_type == "visit"
        }
        target = max(2, len(required_visits))
        kept_visits = set(required_visits)
        for place_id in balanced_order:
            if place_by_id[place_id].activity_type != "visit":
                continue
            if len(kept_visits) >= target:
                break
            kept_visits.add(place_id)
        return tuple(
            place_id
            for place_id in balanced_order
            if place_by_id[place_id].activity_type != "visit"
            or place_id in kept_visits
        )

    @staticmethod
    def _orders_are_materially_different(
        first: tuple[str, ...], second: tuple[str, ...]
    ) -> bool:
        union = set(first) | set(second)
        jaccard = len(set(first) & set(second)) / len(union) if union else 1.0
        longest = max(len(first), len(second), 1)
        same_positions = sum(
            left == right for left, right in zip(first, second, strict=False)
        )
        return jaccard <= 0.8 or same_positions / longest <= 0.7

    def _bus_only_strategy_solutions(
        self,
        request: RecommendDayTripsInput,
        strategy: Strategy,
        place_by_id: dict[str, VerifiedGenerationPlace],
        legs: tuple[_BusCandidateLeg, ...],
    ) -> tuple[tuple[datetime, tuple[str, ...]], ...]:
        """공식 시각과 정책 체류시간만으로 bounded 일정 뼈대를 탐색한다."""

        start_place_id = request.start_boundary.place_id
        end_place_id = request.end_boundary.place_id
        if start_place_id is None or end_place_id is None:
            return ()
        by_edge: dict[tuple[str, str], list[_BusCandidateLeg]] = {}
        for leg in legs:
            by_edge.setdefault((leg.origin_id, leg.destination_id), []).append(leg)
        for candidates in by_edge.values():
            candidates.sort(
                key=lambda item: (
                    item.scheduled_arrival_at,
                    item.access_distance_meters + item.egress_distance_meters,
                    item.transfers,
                    item.scheduled_departure_at,
                )
            )

        required_ids = {
            item.place_id for item in request.required_places if item.place_id is not None
        }
        required_visit_ids = {
            place_id
            for place_id in required_ids
            if place_id in place_by_id
            and place_by_id[place_id].activity_type == "visit"
        }
        visit_target = max(
            {Strategy.RELAXED: 2, Strategy.BALANCED: 3, Strategy.EXPERIENCE_MAX: 3}[
                strategy
            ],
            len(required_visit_ids),
        )
        roles = ["visit"] * visit_target
        if request.food.auto_schedule_meals:
            roles.append("meal")
        if request.food.auto_schedule_cafe:
            roles.append("rest")
        role_patterns = sorted(set(itertools.permutations(roles)))
        lunch_start = datetime.combine(
            request.trip_date,
            self._planning_policy.meal_windows.lunch_start,
            tzinfo=KST,
        )
        lunch_end = datetime.combine(
            request.trip_date,
            self._planning_policy.meal_windows.lunch_end,
            tzinfo=KST,
        )
        results: list[tuple[datetime, tuple[str, ...]]] = []
        transfer_slack_minutes = strategy_transfer_slack_minutes(
            self._planning_policy, strategy
        )

        for use_minimum_stays in (False, True):
            for pattern in role_patterns:
                states: list[tuple[str, datetime, tuple[str, ...], int]] = [
                    (start_place_id, request.activity_window.start_at, (), 0)
                ]
                for role in pattern:
                    next_states: dict[
                        tuple[str, frozenset[str]],
                        tuple[str, datetime, tuple[str, ...], int],
                    ] = {}
                    choices = tuple(
                        sorted(
                            (
                                place
                                for place in place_by_id.values()
                                if place.activity_type == role
                            ),
                            key=lambda place: (
                                place.place_id not in required_ids,
                                place.operating_hours_status != "VERIFIED",
                                place.place_id,
                            ),
                        )
                    )
                    for current_id, current_at, used, continuous_minutes in states:
                        for place in choices:
                            if place.place_id in used:
                                continue
                            arrival = self._next_bus_arrival(
                                by_edge.get((current_id, place.place_id), ()),
                                current_at,
                                request,
                            )
                            if arrival is None:
                                continue
                            activity_start = arrival + timedelta(
                                minutes=transfer_slack_minutes
                            )
                            if place.opens_at is not None:
                                activity_start = max(activity_start, place.opens_at)
                            if role == "meal":
                                activity_start = max(activity_start, lunch_start)
                            stay_minutes = (
                                self._minimum_stay_minutes(place)
                                if use_minimum_stays
                                and role == "visit"
                                and place.place_id not in required_ids
                                else place.stay_minutes
                            )
                            activity_end = activity_start + timedelta(minutes=stay_minutes)
                            if (
                                place.last_admission_at is not None
                                and activity_start > place.last_admission_at
                            ) or (
                                place.closes_at is not None
                                and activity_end > place.closes_at
                            ):
                                continue
                            if role == "meal" and (
                                activity_end > lunch_end
                                or (
                                    place.last_order_at is not None
                                    and activity_start > place.last_order_at
                                )
                            ):
                                continue
                            leg_minutes = max(
                                0,
                                math.ceil((arrival - current_at).total_seconds() / 60),
                            )
                            next_continuous = (
                                0
                                if role in {"meal", "rest"}
                                else continuous_minutes + leg_minutes + stay_minutes
                            )
                            next_used = (*used, place.place_id)
                            key = (place.place_id, frozenset(next_used))
                            candidate = (
                                place.place_id,
                                activity_end,
                                next_used,
                                next_continuous,
                            )
                            existing = next_states.get(key)
                            if existing is None or (
                                activity_end,
                                next_continuous,
                                next_used,
                            ) < (existing[1], existing[3], existing[2]):
                                next_states[key] = candidate
                    states = sorted(
                        next_states.values(),
                        key=lambda item: (item[1], item[3], item[2]),
                    )[:12_000]
                    if not states:
                        break
                for current_id, current_at, used, continuous_minutes in states:
                    if not required_ids.issubset(used):
                        continue
                    if (
                        hard_rest_limit_required(request)
                        and continuous_minutes
                        > request.rest.max_continuous_activity_minutes
                    ):
                        continue
                    return_at = self._next_bus_arrival(
                        by_edge.get((current_id, end_place_id), ()),
                        current_at,
                        request,
                    )
                    if return_at is None or return_at > request.activity_window.end_at:
                        continue
                    results.append((return_at, used))
            if results:
                break
        unique: dict[tuple[str, ...], datetime] = {}
        for return_at, order in results:
            existing = unique.get(order)
            if existing is None or return_at < existing:
                unique[order] = return_at
        return tuple(
            (return_at, order)
            for order, return_at in sorted(
                unique.items(),
                key=lambda item: (
                    sum(
                        place_by_id[place_id].operating_hours_status != "VERIFIED"
                        for place_id in item[0]
                    ),
                    item[1],
                    item[0],
                ),
            )[:80]
        )

    def _next_bus_arrival(
        self,
        legs: Collection[_BusCandidateLeg],
        current_at: datetime,
        request: RecommendDayTripsInput,
    ) -> datetime | None:
        speed_meters_per_minute = (
            self._planning_policy.walking_route_prefilter.maximum_planning_speed_kph
            * 1000
            / 60
        )
        uncertainty = self._planning_policy.boarding_buffer_minutes.route_uncertainty
        boarding_buffer = self._planning_policy.boarding_buffer_minutes.normal
        feasible: list[tuple[datetime, int, int, datetime, str]] = []
        for leg in legs:
            access_minutes = (
                math.ceil(leg.access_distance_meters / speed_meters_per_minute) + uncertainty
            )
            egress_minutes = (
                math.ceil(leg.egress_distance_meters / speed_meters_per_minute) + uncertainty
            )
            if max(access_minutes, egress_minutes) > request.walking.max_access_walk_minutes:
                continue
            if leg.scheduled_departure_at < current_at + timedelta(
                minutes=access_minutes + boarding_buffer
            ):
                continue
            wait_minutes = math.ceil(
                (
                    leg.scheduled_departure_at
                    - current_at
                    - timedelta(minutes=access_minutes)
                ).total_seconds()
                / 60
            )
            if wait_minutes > request.transport.bus_wait_limit_minutes:
                continue
            feasible.append(
                (
                    leg.scheduled_arrival_at + timedelta(minutes=egress_minutes),
                    access_minutes + egress_minutes,
                    leg.transfers,
                    leg.scheduled_departure_at,
                    f"{leg.origin_id}>{leg.destination_id}",
                )
            )
        return min(feasible)[0] if feasible else None

    def _minimum_stay_minutes(self, place: VerifiedGenerationPlace) -> int:
        stay_key = self._stay_policy_key(place.category)
        return self._planning_policy.stay_minutes[stay_key].minimum

    def _bus_candidate_legs(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> tuple[_BusCandidateLeg, ...]:
        """후보 endpoint의 nearest 12 stop 사이 exact 직통·1회 환승을 읽는다."""

        boundaries = (request.start_boundary, request.end_boundary)
        if any(item.place_id is None or item.coordinates is None for item in boundaries):
            return ()
        endpoints = {
            item.place_id: item.coordinates
            for item in boundaries
            if item.place_id is not None and item.coordinates is not None
        }
        endpoints.update({place.place_id: place.position for place in places})
        endpoint_ids = list(endpoints)
        latitudes = [endpoints[place_id].latitude for place_id in endpoint_ids]
        longitudes = [endpoints[place_id].longitude for place_id in endpoint_ids]
        day_type = (
            "SATURDAY"
            if request.trip_date.isoweekday() == 6
            else "SUNDAY"
            if request.trip_date.isoweekday() == 7
            else "WEEKDAY"
        )
        with psycopg.connect(self._runtime_dsn) as connection:
            if connection.execute(
                """SELECT 1 FROM travel_read.active_holiday
                   WHERE holiday_date = %s AND is_public_institution_holiday LIMIT 1""",
                (request.trip_date,),
            ).fetchone() is not None:
                day_type = "HOLIDAY"
            stop_rows = connection.execute(
                """WITH endpoint AS (
                     SELECT *
                     FROM unnest(
                       %(endpoint_ids)s::text[], %(latitudes)s::double precision[],
                       %(longitudes)s::double precision[]
                     ) AS value(place_id, latitude, longitude)
                   ), ranked AS (
                     SELECT endpoint.place_id, stop.fact_id AS stop_fact_id,
                            ST_Distance(
                              stop.position,
                              ST_SetSRID(ST_MakePoint(endpoint.longitude, endpoint.latitude),
                                         4326)::geography
                            ) AS distance_meters,
                            row_number() OVER (
                              PARTITION BY endpoint.place_id
                              ORDER BY ST_Distance(
                                stop.position,
                                ST_SetSRID(ST_MakePoint(endpoint.longitude, endpoint.latitude),
                                           4326)::geography
                              ), stop.fact_id
                            ) AS stop_rank
                     FROM endpoint
                     JOIN travel_read.active_bus_stop stop
                       ON ST_DWithin(
                         stop.position,
                         ST_SetSRID(ST_MakePoint(endpoint.longitude, endpoint.latitude),
                                    4326)::geography,
                         2500
                       )
                     JOIN travel_read.active_stop_identity identity
                      ON identity.source_fact_id = stop.fact_id
                      AND identity.mapping_status = 'CONFIRMED'
                   )
                   SELECT place_id, stop_fact_id, distance_meters
                   FROM ranked
                   WHERE stop_rank <= 12
                   ORDER BY place_id, stop_rank, stop_fact_id""",
                {
                    "endpoint_ids": endpoint_ids,
                    "latitudes": latitudes,
                    "longitudes": longitudes,
                },
            ).fetchall()
            stop_ids = tuple(dict.fromkeys(str(row[1]) for row in stop_rows))
            if not stop_ids:
                return ()
            trip_rows = connection.execute(
                """WITH eligible_trip AS MATERIALIZED (
                     SELECT trip.*
                     FROM travel_read.active_scheduled_trip trip
                     JOIN travel_read.active_service_calendar calendar
                       ON calendar.service_id = trip.service_id
                     WHERE calendar.day_type = %(day_type)s
                       AND calendar.starts_on <= %(trip_date)s
                       AND calendar.ends_on >= %(trip_date)s
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
                     SELECT trip.fact_id AS path_signature,
                            board_time.stop_fact_id AS boarding_stop_fact_id,
                            alight_time.stop_fact_id AS alighting_stop_fact_id,
                            (%(trip_date)s::date + board_time.departure_at
                              + board_time.departure_day_offset * interval '1 day')
                              AT TIME ZONE 'Asia/Seoul' AS scheduled_departure_at,
                            (%(trip_date)s::date + alight_time.arrival_at
                              + alight_time.arrival_day_offset * interval '1 day')
                              AT TIME ZONE 'Asia/Seoul' AS scheduled_arrival_at,
                            0 AS transfers
                     FROM eligible_trip trip
                     JOIN travel_read.active_stop_time board_time
                       ON board_time.trip_id = trip.trip_id
                      AND board_time.stop_fact_id = ANY(%(stop_ids)s::text[])
                     JOIN travel_read.active_stop_time alight_time
                       ON alight_time.trip_id = trip.trip_id
                      AND alight_time.stop_sequence > board_time.stop_sequence
                      AND alight_time.stop_fact_id = ANY(%(stop_ids)s::text[])
                     WHERE NOT EXISTS (
                         SELECT 1
                         FROM travel_read.active_route_service_exception exception
                         WHERE exception.route_fact_id = trip.route_fact_id
                           AND exception.exception_type = 'SUSPENDED'
                           AND exception.starts_at <=
                             (%(trip_date)s::date + board_time.departure_at
                               + board_time.departure_day_offset * interval '1 day')
                               AT TIME ZONE 'Asia/Seoul'
                           AND (exception.ends_at IS NULL OR exception.ends_at >=
                             (%(trip_date)s::date + board_time.departure_at
                               + board_time.departure_day_offset * interval '1 day')
                               AT TIME ZONE 'Asia/Seoul')
                       )
                       AND (%(trip_date)s::date + board_time.departure_at
                         + board_time.departure_day_offset * interval '1 day')
                         AT TIME ZONE 'Asia/Seoul' >= %(starts_at)s
                       AND (%(trip_date)s::date + alight_time.arrival_at
                         + alight_time.arrival_day_offset * interval '1 day')
                         AT TIME ZONE 'Asia/Seoul' <= %(ends_at)s + interval '4 hours'
                   ORDER BY trip.fact_id, board_time.stop_sequence,
                            alight_time.stop_sequence""",
                {
                    "stop_ids": list(stop_ids),
                    "day_type": day_type,
                    "trip_date": request.trip_date,
                    "starts_at": request.activity_window.start_at,
                    "ends_at": request.activity_window.end_at,
                },
            ).fetchall()
            schedule_rows = (
                connection.execute(
                    """WITH eligible_trip AS MATERIALIZED (
                         SELECT trip.*, route.route_number
                         FROM travel_read.active_scheduled_trip trip
                         JOIN travel_read.active_service_calendar calendar
                           ON calendar.service_id = trip.service_id
                         JOIN travel_read.active_bus_route route
                           ON route.fact_id = trip.route_fact_id
                         WHERE calendar.day_type = %(day_type)s
                           AND calendar.starts_on <= %(trip_date)s
                           AND calendar.ends_on >= %(trip_date)s
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
                       SELECT trip.fact_id, trip.route_number, stop_time.stop_fact_id,
                              stop_time.stop_sequence,
                              (%(trip_date)s::date + stop_time.arrival_at
                                + stop_time.arrival_day_offset * interval '1 day')
                                AT TIME ZONE 'Asia/Seoul' AS arrival_at,
                              (%(trip_date)s::date + stop_time.departure_at
                                + stop_time.departure_day_offset * interval '1 day')
                                AT TIME ZONE 'Asia/Seoul' AS departure_at,
                              ST_Y(stop.position::geometry) AS latitude,
                              ST_X(stop.position::geometry) AS longitude
                       FROM eligible_trip trip
                       JOIN travel_read.active_stop_time stop_time
                         ON stop_time.trip_id = trip.trip_id
                       JOIN travel_read.active_bus_stop stop
                         ON stop.fact_id = stop_time.stop_fact_id
                       WHERE EXISTS (
                         SELECT 1 FROM travel_read.active_stop_identity identity
                         WHERE identity.source_fact_id = stop.fact_id
                           AND identity.mapping_status = 'CONFIRMED'
                       )
                       ORDER BY trip.fact_id, stop_time.stop_sequence""",
                    {
                        "day_type": day_type,
                        "trip_date": request.trip_date,
                    },
                ).fetchall()
                if request.transport.max_transfers_per_leg >= 1
                else ()
            )
            transfer_rows = self._one_transfer_candidate_rows(
                schedule_rows,
                set(stop_ids),
                request,
            )
            trip_rows = (*trip_rows, *transfer_rows)
        places_by_stop: dict[str, list[tuple[str, float]]] = {}
        for place_id, stop_id, distance_meters in stop_rows:
            places_by_stop.setdefault(str(stop_id), []).append(
                (str(place_id), float(distance_meters))
            )
        best: dict[tuple[str, str, str], _BusCandidateLeg] = {}
        for (
            trip_fact_id,
            boarding_stop_id,
            alighting_stop_id,
            departure_at,
            arrival_at,
            transfers,
        ) in trip_rows:
            for origin_id, access_distance in places_by_stop[str(boarding_stop_id)]:
                for destination_id, egress_distance in places_by_stop[str(alighting_stop_id)]:
                    if origin_id == destination_id:
                        continue
                    key = (origin_id, destination_id, str(trip_fact_id))
                    candidate = _BusCandidateLeg(
                        origin_id=origin_id,
                        destination_id=destination_id,
                        scheduled_departure_at=departure_at.astimezone(KST),
                        scheduled_arrival_at=arrival_at.astimezone(KST),
                        access_distance_meters=access_distance,
                        egress_distance_meters=egress_distance,
                        transfers=int(transfers),
                    )
                    existing = best.get(key)
                    if existing is None or access_distance + egress_distance < (
                        existing.access_distance_meters + existing.egress_distance_meters
                    ):
                        best[key] = candidate
        return tuple(
            sorted(
                best.values(),
                key=lambda item: (
                    item.origin_id,
                    item.destination_id,
                    item.scheduled_departure_at,
                    item.scheduled_arrival_at,
                    item.access_distance_meters + item.egress_distance_meters,
                ),
            )
        )

    def _one_transfer_candidate_rows(
        self,
        rows: Collection[tuple[Any, ...]],
        endpoint_stop_ids: set[str],
        request: RecommendDayTripsInput,
    ) -> tuple[tuple[str, str, str, datetime, datetime, int], ...]:
        """당일 stop-time 행을 메모리 그래프로 묶어 1회 환승 후보를 bounded 탐색한다."""

        calls_by_trip: dict[str, list[_ScheduledStopCall]] = {}
        for row in rows:
            call = _ScheduledStopCall(
                trip_fact_id=str(row[0]),
                route_number=str(row[1]).strip(),
                stop_fact_id=str(row[2]),
                stop_sequence=int(row[3]),
                arrival_at=row[4].astimezone(KST),
                departure_at=row[5].astimezone(KST),
                latitude=float(row[6]),
                longitude=float(row[7]),
            )
            calls_by_trip.setdefault(call.trip_fact_id, []).append(call)
        trips = tuple(
            tuple(sorted(calls, key=lambda item: (item.stop_sequence, item.stop_fact_id)))
            for _, calls in sorted(calls_by_trip.items())
        )
        positions = {
            call.stop_fact_id: (call.latitude, call.longitude)
            for calls in trips
            for call in calls
        }
        neighbors: dict[str, tuple[tuple[str, float], ...]] = {}
        for stop_id, position in positions.items():
            neighbors[stop_id] = tuple(
                sorted(
                    (
                        (candidate_id, distance)
                        for candidate_id, candidate_position in positions.items()
                        if (
                            distance := self._coordinate_distance_meters(
                                position, candidate_position
                            )
                        )
                        <= 300
                    ),
                    key=lambda item: (item[1], item[0]),
                )
            )

        second_by_board: dict[
            str,
            list[tuple[datetime, _ScheduledStopCall, _ScheduledStopCall]],
        ] = {}
        for calls in trips:
            for board_index, board in enumerate(calls[:-1]):
                for alight in calls[board_index + 1 :]:
                    if alight.stop_fact_id not in endpoint_stop_ids:
                        continue
                    second_by_board.setdefault(board.stop_fact_id, []).append(
                        (board.departure_at, board, alight)
                    )
        second_departures: dict[str, tuple[datetime, ...]] = {}
        for stop_id, candidates in second_by_board.items():
            candidates.sort(key=lambda item: (item[0], item[2].arrival_at, item[1].trip_fact_id))
            second_departures[stop_id] = tuple(item[0] for item in candidates)

        speed_meters_per_minute = (
            self._planning_policy.walking_route_prefilter.maximum_planning_speed_kph
            * 1000
            / 60
        )
        latest_arrival = request.activity_window.end_at + timedelta(hours=4)
        candidates_by_bucket: dict[
            tuple[str, str, date, int],
            dict[str, tuple[str, str, str, datetime, datetime, int]],
        ] = {}
        for calls in trips:
            for board_index, first_board in enumerate(calls[:-1]):
                if (
                    first_board.stop_fact_id not in endpoint_stop_ids
                    or first_board.departure_at < request.activity_window.start_at
                    or first_board.departure_at > latest_arrival
                ):
                    continue
                for transfer_from in calls[board_index + 1 :]:
                    for transfer_to_id, transfer_distance in neighbors[
                        transfer_from.stop_fact_id
                    ]:
                        transfer_walk_minutes = math.ceil(
                            transfer_distance / speed_meters_per_minute
                        )
                        ready_at = transfer_from.arrival_at + timedelta(
                            minutes=transfer_walk_minutes + 10
                        )
                        latest_departure = ready_at + timedelta(
                            minutes=request.transport.bus_wait_limit_minutes
                        )
                        departures = second_departures.get(transfer_to_id, ())
                        second_options = second_by_board.get(transfer_to_id, ())
                        cursor = bisect_left(departures, ready_at)
                        while cursor < len(second_options):
                            second_departure, second_board, final_alight = second_options[cursor]
                            if second_departure > latest_departure:
                                break
                            cursor += 1
                            if (
                                second_board.trip_fact_id == first_board.trip_fact_id
                                or second_board.route_number == first_board.route_number
                                or final_alight.arrival_at > latest_arrival
                            ):
                                continue
                            signature = (
                                f"{first_board.trip_fact_id}>{second_board.trip_fact_id}"
                            )
                            candidate = (
                                signature,
                                first_board.stop_fact_id,
                                final_alight.stop_fact_id,
                                first_board.departure_at,
                                final_alight.arrival_at,
                                1,
                            )
                            bucket_key = (
                                first_board.stop_fact_id,
                                final_alight.stop_fact_id,
                                first_board.departure_at.date(),
                                first_board.departure_at.hour,
                            )
                            bucket = candidates_by_bucket.setdefault(bucket_key, {})
                            existing = bucket.get(signature)
                            if existing is None or (
                                candidate[4], candidate[3], candidate[0]
                            ) < (existing[4], existing[3], existing[0]):
                                bucket[signature] = candidate
                            if len(bucket) > 3:
                                worst = max(
                                    bucket,
                                    key=lambda key: (
                                        bucket[key][4], bucket[key][3], bucket[key][0]
                                    ),
                                )
                                del bucket[worst]
        return tuple(
            sorted(
                (
                    candidate
                    for bucket in candidates_by_bucket.values()
                    for candidate in bucket.values()
                ),
                key=lambda item: (item[1], item[2], item[3], item[4], item[0]),
            )
        )

    @staticmethod
    def _coordinate_distance_meters(
        first: tuple[float, float], second: tuple[float, float]
    ) -> float:
        """정류장 300m 후보용 결정론적 직선거리 하한을 계산한다."""

        latitude_scale = 111_320.0
        longitude_scale = latitude_scale * math.cos(
            math.radians((first[0] + second[0]) / 2)
        )
        return math.hypot(
            (first[0] - second[0]) * latitude_scale,
            (first[1] - second[1]) * longitude_scale,
        )

    def _clustered_candidate_ids(self, request: RecommendDayTripsInput) -> tuple[str, ...]:
        """제주 전역 active 장소를 8km micro-cluster와 역할별 상한으로 압축한다."""

        accommodation = request.start_boundary.coordinates
        if accommodation is None:
            return ()
        history = history_place_policy(request)
        excluded = {
            item.place_id for item in request.excluded_places if item.place_id is not None
        } | set(history.excluded_visit_ids)
        required = [item.place_id for item in request.required_places if item.place_id is not None]
        bus_only = request.transport.allowed_modes == {"bus"}
        calendar_day_type = (
            "SATURDAY"
            if request.trip_date.isoweekday() == 6
            else "SUNDAY"
            if request.trip_date.isoweekday() == 7
            else "WEEKDAY"
        )
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                """WITH served_stop AS MATERIALIZED (
                     SELECT DISTINCT stop_time.stop_fact_id
                     FROM travel_read.active_scheduled_trip trip
                     JOIN travel_read.active_stop_time stop_time
                       ON stop_time.trip_id = trip.trip_id
                     JOIN travel_read.active_stop_identity identity
                       ON identity.source_fact_id = stop_time.stop_fact_id
                      AND identity.mapping_status = 'CONFIRMED'
                     JOIN travel_read.active_service_calendar calendar
                       ON calendar.service_id = trip.service_id
                     WHERE calendar.starts_on <= %(trip_date)s
                       AND calendar.ends_on >= %(trip_date)s
                       AND calendar.day_type = CASE
                         WHEN EXISTS (
                           SELECT 1 FROM travel_read.active_holiday holiday
                           WHERE holiday.holiday_date = %(trip_date)s
                             AND holiday.is_public_institution_holiday
                         ) THEN 'HOLIDAY'
                         ELSE %(calendar_day_type)s
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
                   ), hotel_stop AS MATERIALIZED (
                     SELECT stop.fact_id, stop.position
                     FROM travel_read.active_bus_stop stop
                     JOIN served_stop served ON served.stop_fact_id = stop.fact_id
                     WHERE ST_DWithin(
                       stop.position,
                       ST_SetSRID(
                         ST_MakePoint(%(longitude)s, %(latitude)s), 4326
                       )::geography,
                       2500
                     )
                   ), candidate AS (
                     SELECT place.fact_id, place.position, place.observed_at,
                            CASE
                              WHEN place.attributes->>'category_level_3' = 'A05020900'
                                THEN 'rest'
                              WHEN place.category = '39' THEN 'meal'
                              ELSE 'visit'
                            END AS role,
                            place.category,
                            place.fact_id = ANY(%(required)s::text[]) AS is_required,
                            EXISTS (
                              SELECT 1
                              FROM travel_read.active_place_opening_rule opening
                              JOIN travel_read.active_source_metadata opening_metadata
                                ON opening_metadata.publication_id = opening.publication_id
                              WHERE opening.place_fact_id = place.fact_id
                                AND opening.period_kind = 'OPEN'
                                AND opening.normalization_status = 'VERIFIED'
                                AND (opening.service_day IS NULL
                                     OR opening.service_day = %(service_day)s)
                                AND (opening.valid_from IS NULL
                                     OR opening.valid_from <= %(trip_date)s)
                                AND (opening.valid_to IS NULL
                                     OR opening.valid_to >= %(trip_date)s)
                                AND (
                                  (
                                    opening_metadata.observed_at IS NOT NULL
                                    AND opening_metadata.observed_at
                                      >= CURRENT_TIMESTAMP - interval '8 days'
                                    AND opening_metadata.observed_at
                                      <= CURRENT_TIMESTAMP + interval '5 minutes'
                                  ) OR (
                                    opening_metadata.observed_at IS NULL
                                    AND opening_metadata.source_date
                                      >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date - 8
                                    AND opening_metadata.source_date
                                      <= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
                                  )
                                )
                            ) AS has_applicable_hours,
                            ST_Distance(
                              place.position,
                              ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)
                                ::geography) AS hotel_distance
                     FROM travel_read.active_place place
                     CROSS JOIN travel_read.active_service_area_boundary boundary
                     WHERE place.category <> '32'
                       AND NOT (place.fact_id = ANY(%(excluded)s::text[]))
                       AND (%(allow_visits)s OR place.category = '39'
                            OR place.fact_id = ANY(%(required)s::text[]))
                       AND NOT EXISTS (
                         SELECT 1
                         FROM travel_read.active_place_schedule_exception closure
                         WHERE closure.place_fact_id = place.fact_id
                           AND closure.exception_date = %(trip_date)s
                           AND closure.exception_type = 'CLOSED'
                       )
                       AND NOT EXISTS (
                         SELECT 1
                         FROM travel_read.active_place_weekly_closure weekly_closure
                         WHERE weekly_closure.place_fact_id = place.fact_id
                           AND weekly_closure.service_day = %(service_day)s
                           AND (weekly_closure.valid_from IS NULL
                                OR weekly_closure.valid_from <= %(trip_date)s)
                           AND (weekly_closure.valid_to IS NULL
                                OR weekly_closure.valid_to >= %(trip_date)s)
                       )
                       AND ST_Covers(boundary.geometry, place.position::geometry)
                       AND (
                         NOT %(bus_only)s
                         OR (
                           EXISTS (
                             SELECT 1 FROM hotel_stop
                           )
                           AND EXISTS (
                             SELECT 1
                             FROM travel_read.active_bus_stop stop
                             JOIN served_stop served ON served.stop_fact_id = stop.fact_id
                             WHERE ST_DWithin(stop.position, place.position, 2500)
                           )
                         )
                       )
                   ), eligible_candidate AS (
                     SELECT candidate.*
                     FROM candidate
                     WHERE candidate.category <> '15' OR candidate.has_applicable_hours
                   ), clustered AS (
                     SELECT eligible_candidate.*,
                            ST_ClusterDBSCAN(
                              ST_Transform(position::geometry, 5179),
                              eps := 8000, minpoints := 1) OVER () AS cluster_id
                     FROM eligible_candidate
                   ), cluster_score AS (
                     SELECT cluster_id,
                            bool_or(is_required) AS has_required,
                            min(hotel_distance) AS hotel_distance,
                            count(DISTINCT category) AS category_diversity,
                            max(observed_at) AS newest_observed_at
                     FROM clustered GROUP BY cluster_id
                   ), selected_cluster AS (
                     SELECT cluster_id
                     FROM cluster_score
                     ORDER BY has_required DESC, hotel_distance,
                              category_diversity DESC, newest_observed_at DESC, cluster_id
                     LIMIT 6
                   ), ranked AS (
                     SELECT clustered.*,
                            row_number() OVER (
                              PARTITION BY clustered.cluster_id, clustered.role
                              ORDER BY clustered.is_required DESC,
                                       clustered.has_applicable_hours DESC,
                                       CASE WHEN %(bus_only)s
                                         THEN clustered.hotel_distance END,
                                       clustered.observed_at DESC,
                                       CASE WHEN NOT %(bus_only)s
                                         THEN clustered.fact_id END,
                                       clustered.hotel_distance,
                                       clustered.fact_id) AS hours_role_rank,
                            row_number() OVER (
                              PARTITION BY clustered.cluster_id, clustered.role
                              ORDER BY clustered.is_required DESC,
                                       CASE WHEN %(bus_only)s
                                         THEN clustered.hotel_distance END,
                                       clustered.observed_at DESC,
                                       CASE WHEN NOT %(bus_only)s
                                         THEN clustered.fact_id END,
                                       clustered.hotel_distance,
                                       clustered.fact_id) AS local_role_rank
                     FROM clustered
                     JOIN selected_cluster USING (cluster_id)
                   ), candidate_pool AS (
                     SELECT ranked.*,
                            row_number() OVER (
                              PARTITION BY ranked.role
                              ORDER BY LEAST(
                                         ranked.hours_role_rank,
                                         ranked.local_role_rank),
                                       ranked.has_applicable_hours DESC,
                                       ranked.hotel_distance,
                                       ranked.cluster_id,
                                       ranked.fact_id) AS candidate_pool_rank
                     FROM ranked
                     WHERE (role = 'visit' AND
                              (hours_role_rank <= 8 OR local_role_rank <= 8))
                        OR (role = 'meal' AND
                              (hours_role_rank <= 5 OR local_role_rank <= 5))
                        OR (role = 'rest' AND
                              (hours_role_rank <= 3 OR local_role_rank <= 3))
                   )
                   SELECT fact_id
                   FROM candidate_pool
                   WHERE (role = 'visit' AND candidate_pool_rank <= 16)
                      OR (role = 'meal' AND candidate_pool_rank <= 10)
                      OR (role = 'rest' AND candidate_pool_rank <= 6)
                   ORDER BY role, candidate_pool_rank, fact_id""",
                {
                    "required": required,
                    "excluded": sorted(excluded),
                    "allow_visits": request.discovery.allow_additional_attractions,
                    "bus_only": bus_only,
                    "trip_date": request.trip_date,
                    "service_day": request.trip_date.isoweekday(),
                    "calendar_day_type": calendar_day_type,
                    "longitude": accommodation.longitude,
                    "latitude": accommodation.latitude,
                },
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _discovery_ids(
        self, request: RecommendDayTripsInput, excluded: set[str]
    ) -> tuple[str, ...]:
        limit = request.discovery.maximum_additional_places
        content_types = tuple(
            dict.fromkeys(
                content_type
                for category in request.discovery.preferred_categories
                for content_type in self._content_types_for_discovery_category(category)
            )
        )
        if request.discovery.preferred_categories and not content_types:
            return ()
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                """SELECT place.fact_id FROM travel_read.active_place place
                   CROSS JOIN travel_read.active_service_area_boundary boundary
                   WHERE NOT (place.fact_id = ANY(%s::text[]))
                     AND place.category <> '39'
                     AND (cardinality(%s::text[]) = 0 OR place.category = ANY(%s::text[]))
                     AND ST_Covers(boundary.geometry, place.position::geometry)
                   ORDER BY place.fact_id LIMIT %s""",
                (
                    list(excluded),
                    list(content_types),
                    list(content_types),
                    limit,
                ),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _content_types_for_discovery_category(self, category: str) -> tuple[str, ...]:
        """공개 의미 카테고리를 TourAPI contentTypeId에만 명시적으로 대응한다."""

        return self._planning_policy.discovery_content_types.get(category.strip().lower(), ())

    def _prime_place_candidates(
        self, place_ids: tuple[str, ...], request: RecommendDayTripsInput
    ) -> None:
        """후보별 연결·쿼리 반복 없이 장소·운영시간·입구 스냅샷을 네 번에 읽는다."""

        if not place_ids:
            return
        entrance_type_clause = (
            "AND entrance.entrance_type = 'accessible'"
            if request.party.mobility_support_required
            else ""
        )
        entrance_statement = f"""
        SELECT entrance.place_fact_id, entrance.fact_id, entrance.entrance_id,
               ST_Y(entrance.position::geometry), ST_X(entrance.position::geometry),
               entrance.supported_modes, entrance.publication_id::text,
               entrance.last_verified_at
        FROM travel_read.active_place_entrance entrance
        CROSS JOIN travel_read.active_service_area_boundary boundary
        WHERE entrance.place_fact_id = ANY(%s::text[])
          AND entrance.verification_status = 'VERIFIED'
          AND (entrance.verification_expires_at IS NULL
               OR entrance.verification_expires_at >= %s)
          AND entrance.supported_modes && %s
          AND ST_Covers(boundary.geometry, entrance.position::geometry)
          {entrance_type_clause}
        ORDER BY entrance.place_fact_id,
                 CASE entrance.entrance_type
                   WHEN 'accessible' THEN 0 WHEN 'main' THEN 1 ELSE 2 END,
                 entrance.entrance_id
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            place_rows = connection.execute(
                """SELECT place.fact_id, place.source_id, place.publication_id::text,
                          place.name, place.category, place.address,
                          ST_Y(place.position::geometry), ST_X(place.position::geometry),
                          place.data_as_of, place.observed_at,
                          place.attributes->>'category_level_3',
                          observation.fact_id, observation.observation_status,
                          observation.reason_code, observation.publication_id::text,
                          observation_metadata.source_id,
                          observation_metadata.source_date,
                          observation_metadata.observed_at
                   FROM travel_read.active_place place
                   LEFT JOIN travel_read.active_place_opening_observation observation
                     ON observation.place_fact_id = place.fact_id
                   LEFT JOIN travel_read.active_source_metadata observation_metadata
                     ON observation_metadata.publication_id = observation.publication_id
                   CROSS JOIN travel_read.active_service_area_boundary boundary
                   WHERE place.fact_id = ANY(%s::text[])
                     AND ST_Covers(boundary.geometry, place.position::geometry)""",
                (list(place_ids),),
            ).fetchall()
            opening_rows = connection.execute(
                """SELECT value.place_fact_id, value.fact_id,
                          value.publication_id::text, opens_minute,
                          closes_minute, closes_day_offset, last_admission_minute,
                          last_order_minute, source_refs, metadata.source_id,
                          metadata.source_date, metadata.observed_at, period_kind
                   FROM travel_read.active_place_opening_rule value
                   JOIN travel_read.active_source_metadata metadata
                     ON metadata.publication_id = value.publication_id
                   WHERE value.place_fact_id = ANY(%s::text[])
                     AND period_kind IN ('OPEN', 'BREAK')
                     AND normalization_status = 'VERIFIED'
                     AND (service_day IS NULL OR service_day = %s)
                     AND (valid_from IS NULL OR valid_from <= %s)
                     AND (valid_to IS NULL OR valid_to >= %s)
                   ORDER BY value.place_fact_id, period_kind DESC,
                            opens_minute, value.fact_id""",
                (
                    list(place_ids),
                    request.trip_date.isoweekday(),
                    request.trip_date,
                    request.trip_date,
                ),
            ).fetchall()
            exception_rows = connection.execute(
                """SELECT DISTINCT ON (value.place_fact_id)
                          value.place_fact_id, value.fact_id, value.exception_type,
                          value.opens_minute, value.closes_minute,
                          value.closes_day_offset, value.source_refs,
                          value.publication_id::text, metadata.source_id,
                          metadata.source_date, metadata.observed_at
                   FROM (
                     SELECT exact.place_fact_id, exact.fact_id, exact.exception_type,
                            exact.opens_minute, exact.closes_minute,
                            exact.closes_day_offset, exact.source_refs,
                            exact.publication_id, 0 AS priority
                     FROM travel_read.active_place_schedule_exception exact
                     WHERE exact.place_fact_id = ANY(%s::text[])
                       AND exact.exception_date = %s
                     UNION ALL
                     SELECT weekly.place_fact_id, weekly.fact_id, 'CLOSED'::text,
                            NULL::smallint, NULL::smallint, 0::smallint,
                            weekly.source_refs, weekly.publication_id, 1 AS priority
                     FROM travel_read.active_place_weekly_closure weekly
                     WHERE weekly.place_fact_id = ANY(%s::text[])
                       AND weekly.service_day = %s
                       AND (weekly.valid_from IS NULL OR weekly.valid_from <= %s)
                       AND (weekly.valid_to IS NULL OR weekly.valid_to >= %s)
                   ) value
                   JOIN travel_read.active_source_metadata metadata
                     ON metadata.publication_id = value.publication_id
                   ORDER BY value.place_fact_id, value.priority, value.fact_id""",
                (
                    list(place_ids),
                    request.trip_date,
                    list(place_ids),
                    request.trip_date.isoweekday(),
                    request.trip_date,
                    request.trip_date,
                ),
            ).fetchall()
            entrance_rows = connection.execute(
                sql.SQL(cast(LiteralString, entrance_statement)),
                (
                    list(place_ids),
                    request.activity_window.start_at,
                    list(request.transport.allowed_modes),
                ),
            ).fetchall()

        self._place_rows = {place_id: None for place_id in place_ids}
        self._place_rows.update({str(row[0]): tuple(row) for row in place_rows})
        grouped_openings: dict[str, list[tuple[Any, ...]]] = {
            place_id: [] for place_id in place_ids
        }
        for row in opening_rows:
            grouped_openings.setdefault(str(row[0]), []).append(tuple(row[1:]))
        self._opening_rows = {
            place_id: tuple(rows) for place_id, rows in grouped_openings.items()
        }
        self._exception_rows = {place_id: None for place_id in place_ids}
        self._exception_rows.update({str(row[0]): tuple(row[1:]) for row in exception_rows})
        grouped_entrances: dict[str, list[tuple[Any, ...]]] = {
            place_id: [] for place_id in place_ids
        }
        for row in entrance_rows:
            grouped_entrances.setdefault(str(row[0]), []).append(tuple(row[1:]))
        self._entrance_rows = {
            place_id: tuple(rows) for place_id, rows in grouped_entrances.items()
        }

    def _load_place(
        self, place_id: str, request: RecommendDayTripsInput
    ) -> VerifiedGenerationPlace | None:
        place_rows = getattr(self, "_place_rows", {})
        if place_id in place_rows:
            place = place_rows[place_id]
            opening_rows = getattr(self, "_opening_rows", {}).get(place_id, ())
            exception = getattr(self, "_exception_rows", {}).get(place_id)
        else:
            with psycopg.connect(self._runtime_dsn) as connection:
                place = connection.execute(
                    """SELECT place.fact_id, place.source_id, place.publication_id::text,
                              place.name, place.category, place.address,
                              ST_Y(place.position::geometry), ST_X(place.position::geometry),
                              place.data_as_of, place.observed_at,
                              place.attributes->>'category_level_3',
                              observation.fact_id, observation.observation_status,
                              observation.reason_code, observation.publication_id::text,
                              observation_metadata.source_id,
                              observation_metadata.source_date,
                              observation_metadata.observed_at
                       FROM travel_read.active_place place
                       LEFT JOIN travel_read.active_place_opening_observation observation
                         ON observation.place_fact_id = place.fact_id
                       LEFT JOIN travel_read.active_source_metadata observation_metadata
                         ON observation_metadata.publication_id = observation.publication_id
                       CROSS JOIN travel_read.active_service_area_boundary boundary
                       WHERE place.fact_id = %s
                         AND ST_Covers(boundary.geometry, place.position::geometry)""",
                    (place_id,),
                ).fetchone()
                opening_rows = connection.execute(
                    """SELECT value.fact_id, value.publication_id::text, opens_minute,
                              closes_minute, closes_day_offset, last_admission_minute,
                              last_order_minute,
                              source_refs, metadata.source_id, metadata.source_date,
                              metadata.observed_at, period_kind
                       FROM travel_read.active_place_opening_rule value
                       JOIN travel_read.active_source_metadata metadata
                         ON metadata.publication_id = value.publication_id
                       WHERE place_fact_id = %s
                         AND period_kind IN ('OPEN', 'BREAK')
                         AND normalization_status = 'VERIFIED'
                         AND (service_day IS NULL OR service_day = %s)
                         AND (valid_from IS NULL OR valid_from <= %s)
                         AND (valid_to IS NULL OR valid_to >= %s)
                       ORDER BY period_kind DESC, opens_minute, value.fact_id""",
                    (
                        place_id,
                        request.trip_date.isoweekday(),
                        request.trip_date,
                        request.trip_date,
                    ),
                ).fetchall()
                exception = connection.execute(
                    """SELECT value.fact_id, value.exception_type, value.opens_minute,
                              value.closes_minute, value.closes_day_offset,
                              value.source_refs, value.publication_id::text,
                              metadata.source_id, metadata.source_date, metadata.observed_at
                       FROM (
                         SELECT exact.fact_id, exact.exception_type, exact.opens_minute,
                                exact.closes_minute, exact.closes_day_offset,
                                exact.source_refs, exact.publication_id, 0 AS priority
                         FROM travel_read.active_place_schedule_exception exact
                         WHERE exact.place_fact_id = %s AND exact.exception_date = %s
                         UNION ALL
                         SELECT weekly.fact_id, 'CLOSED'::text, NULL::smallint,
                                NULL::smallint, 0::smallint, weekly.source_refs,
                                weekly.publication_id, 1 AS priority
                         FROM travel_read.active_place_weekly_closure weekly
                         WHERE weekly.place_fact_id = %s AND weekly.service_day = %s
                           AND (weekly.valid_from IS NULL OR weekly.valid_from <= %s)
                           AND (weekly.valid_to IS NULL OR weekly.valid_to >= %s)
                       ) value
                       JOIN travel_read.active_source_metadata metadata
                         ON metadata.publication_id = value.publication_id
                       ORDER BY value.priority LIMIT 1""",
                    (
                        place_id,
                        request.trip_date,
                        place_id,
                        request.trip_date.isoweekday(),
                        request.trip_date,
                        request.trip_date,
                    ),
                ).fetchone()
        if place is None or (exception and exception[1] == "CLOSED"):
            return None
        place_row = cast(Sequence[Any], place)
        if not self._source_is_fresh(
            str(place_row[1]), source_date=place_row[8], observed_at=place_row[9]
        ):
            return None
        observation = (
            tuple(place_row[11:18])
            if len(place_row) >= 18 and place_row[11] is not None
            else None
        )
        special_exception = (
            exception if exception is not None and exception[1] == "SPECIAL_HOURS" else None
        )
        day_start = datetime.combine(request.trip_date, datetime.min.time(), tzinfo=KST)
        fresh_opening_rows = tuple(
            tuple(row)
            for row in opening_rows
            if self._fresh_enough(row[10], 8, source_date=row[9])
        )
        exception_is_fresh = special_exception is not None and self._fresh_enough(
            special_exception[9], 8, source_date=special_exception[8]
        )
        if exception_is_fresh and special_exception is not None:
            exception_window = VerifiedOpeningWindow(
                opens_at=day_start + timedelta(minutes=int(special_exception[2])),
                closes_at=day_start
                + timedelta(
                    days=int(special_exception[4]),
                    minutes=int(special_exception[3]),
                ),
                evidence_fact_ids=(str(special_exception[0]),),
            )
            opening_windows = _subtract_verified_breaks(
                (exception_window,),
                tuple(row for row in fresh_opening_rows if row[11] == "BREAK"),
                day_start,
            )
        else:
            opening_windows = _verified_opening_windows(fresh_opening_rows, day_start)
        has_fresh_open_rule = any(row[11] == "OPEN" for row in fresh_opening_rows)
        if (exception_is_fresh or has_fresh_open_rule) and not opening_windows:
            return None
        hours_verified = bool(opening_windows)
        selected_opening = opening_windows[0] if opening_windows else None
        opens_at = selected_opening.opens_at if selected_opening is not None else None
        closes_at = selected_opening.closes_at if selected_opening is not None else None
        last_admission = (
            selected_opening.last_admission_at if selected_opening is not None else None
        )
        last_order = selected_opening.last_order_at if selected_opening is not None else None
        entrances = self._load_route_endpoints(
            place_id,
            request,
            representative_position=Coordinates(
                latitude=float(place_row[6]), longitude=float(place_row[7])
            ),
        )
        if not entrances:
            return None
        self._entrances[place_id] = entrances
        category = str(place_row[4])
        detailed_category = str(place_row[10]) if place_row[10] is not None else None
        stay_key = self._stay_policy_key(category, detailed_category)
        stay = self._planning_policy.stay_minutes[stay_key].recommended
        place_fact_id = str(place_row[0])
        self._add_source_fact(
            place_fact_id,
            "place",
            {
                "name": str(place_row[3]),
                "address": str(place_row[5]),
                "content_type_id": category,
                "category_level_3": detailed_category,
            },
            str(place_row[1]),
            str(place_row[2]),
            place_row[8],
            place_row[9],
        )
        opening_fact_ids: list[str] = []
        if hours_verified:
            assert opens_at is not None and closes_at is not None
            if exception_is_fresh and special_exception is not None:
                exception_fact_id = str(special_exception[0])
                opening_fact_ids.append(exception_fact_id)
                exception_observed_at = special_exception[9] or datetime.now(UTC)
                self._add_source_fact(
                    exception_fact_id,
                    "opening_hours",
                    {"opens_at": opens_at.isoformat(), "closes_at": closes_at.isoformat()},
                    str(special_exception[7]),
                    str(special_exception[6]),
                    special_exception[8] or exception_observed_at.date(),
                    exception_observed_at,
                )
            applicable_opening_rows = (
                tuple(row for row in fresh_opening_rows if row[11] == "BREAK")
                if exception_is_fresh
                else fresh_opening_rows
            )
            for opening_row in applicable_opening_rows:
                opening_fact_id = str(opening_row[0])
                opening_fact_ids.append(opening_fact_id)
                period_kind = str(opening_row[11])
                period_opens_at = day_start + timedelta(minutes=int(opening_row[2]))
                period_closes_at = day_start + timedelta(
                    days=int(opening_row[4]), minutes=int(opening_row[3])
                )
                opening_observed_at = opening_row[10] or datetime.now(UTC)
                self._add_source_fact(
                    opening_fact_id,
                    "opening_hours_break" if period_kind == "BREAK" else "opening_hours",
                    {
                        "period_kind": period_kind,
                        "opens_at": period_opens_at.isoformat(),
                        "closes_at": period_closes_at.isoformat(),
                    },
                    str(opening_row[8]),
                    str(opening_row[1]),
                    opening_row[9] or opening_observed_at.date(),
                    opening_observed_at,
                )
        elif observation is not None and self._fresh_enough(
            observation[6], 8, source_date=observation[5]
        ):
            opening_fact_id = str(observation[0])
            opening_fact_ids.append(opening_fact_id)
            observation_retrieved_at = observation[6] or datetime.now(UTC)
            self._add_source_fact(
                opening_fact_id,
                "opening_hours_observation",
                {
                    "observation_status": str(observation[1]),
                    "reason_code": (
                        str(observation[2]) if observation[2] is not None else None
                    ),
                },
                str(observation[4]),
                str(observation[3]),
                observation[5] or observation_retrieved_at.date(),
                observation_retrieved_at,
            )
        for entrance in entrances:
            if entrance.endpoint_basis != EndpointBasis.VERIFIED_ENTRANCE:
                continue
            self._add_source_fact(
                entrance.evidence_fact_ids[0],
                "place_entrance",
                {"entrance_id": entrance.entrance_id},
                "travel.place-entrance-map",
                None,
                request.trip_date,
                datetime.now(UTC),
            )
        stay_fact_id = self._policy_fact_id(f"stay_minutes.{stay_key}")
        return VerifiedGenerationPlace(
            place_id=place_fact_id,
            name=str(place_row[3]),
            position=Coordinates(
                latitude=float(place_row[6]), longitude=float(place_row[7])
            ),
            entrance_id=entrances[0].entrance_id,
            category=category,
            opens_at=opens_at,
            closes_at=closes_at,
            last_admission_at=last_admission,
            stay_minutes=stay,
            is_estimated_stay=True,
            evidence_fact_ids=tuple(
                dict.fromkeys(
                    fact_id
                    for fact_id in (place_fact_id, *opening_fact_ids, stay_fact_id)
                    if fact_id is not None
                )
            ),
            operating_hours_status="VERIFIED" if hours_verified else "UNVERIFIED",
            activity_type=(
                "rest"
                if detailed_category == "A05020900"
                else "meal"
                if category == "39"
                else "visit"
            ),
            last_order_at=last_order,
            opening_windows=opening_windows,
        )

    def _load_entrances(
        self, place_id: str, request: RecommendDayTripsInput
    ) -> tuple[VerifiedEntrance, ...]:
        entrance_type_clause = (
            "AND entrance_type = 'accessible'" if request.party.mobility_support_required else ""
        )
        statement = f"""
        SELECT entrance.fact_id, entrance.entrance_id,
               ST_Y(entrance.position::geometry), ST_X(entrance.position::geometry),
               entrance.supported_modes, entrance.publication_id::text,
               entrance.last_verified_at
        FROM travel_read.active_place_entrance entrance
        CROSS JOIN travel_read.active_service_area_boundary boundary
        WHERE entrance.place_fact_id = %s AND entrance.verification_status = 'VERIFIED'
          AND (entrance.verification_expires_at IS NULL
               OR entrance.verification_expires_at >= %s)
          AND entrance.supported_modes && %s
          AND ST_Covers(boundary.geometry, entrance.position::geometry)
          {entrance_type_clause}
        ORDER BY CASE entrance.entrance_type
                   WHEN 'accessible' THEN 0 WHEN 'main' THEN 1 ELSE 2 END,
                 entrance.entrance_id
        """
        cached_rows = getattr(self, "_entrance_rows", {})
        if place_id in cached_rows:
            rows = cached_rows[place_id]
        else:
            with psycopg.connect(self._runtime_dsn) as connection:
                rows = connection.execute(
                    sql.SQL(cast(LiteralString, statement)),
                    (
                        place_id,
                        request.activity_window.start_at,
                        list(request.transport.allowed_modes),
                    ),
                ).fetchall()
        entrances = tuple(
            VerifiedEntrance(
                entrance_id=str(row[1]),
                place_id=place_id,
                position=Coordinates(latitude=float(row[2]), longitude=float(row[3])),
                supported_modes=tuple(str(mode) for mode in row[4]),
                evidence_fact_ids=(str(row[0]),),
            )
            for row in rows
        )
        publication_by_fact_id = {str(row[0]): str(row[5]) for row in rows}
        verified_by_fact_id = {str(row[0]): row[6] for row in rows}
        for entrance in entrances:
            if entrance.evidence_fact_ids[0] not in self._facts:
                self._add_source_fact(
                    entrance.evidence_fact_ids[0],
                    "place_entrance",
                    {"entrance_id": entrance.entrance_id},
                    "travel.place-entrance-map",
                    publication_by_fact_id[entrance.evidence_fact_ids[0]],
                    verified_by_fact_id[entrance.evidence_fact_ids[0]] or request.trip_date,
                    datetime.now(UTC),
                )
        return entrances

    @staticmethod
    def _representative_endpoint(
        *,
        place_id: str,
        position: Coordinates,
        allowed_modes: Collection[str],
    ) -> VerifiedEntrance:
        """입구가 없는 일반 장소에 TourAPI 대표좌표 endpoint를 명시적으로 만든다."""

        return VerifiedEntrance(
            entrance_id=f"place-point:{place_id}",
            place_id=place_id,
            position=position,
            supported_modes=tuple(sorted(allowed_modes)),
            endpoint_basis=EndpointBasis.REPRESENTATIVE_PLACE_POINT,
            evidence_fact_ids=(place_id,),
        )

    def _load_route_endpoints(
        self,
        place_id: str,
        request: RecommendDayTripsInput,
        *,
        representative_position: Coordinates | None = None,
    ) -> tuple[VerifiedEntrance, ...]:
        """검증 입구를 우선하고 일반 요청에만 active 장소 대표좌표를 보충한다."""

        if not self._active_place_is_fresh(place_id):
            return ()
        entrances = self._load_entrances(place_id, request)
        if entrances:
            return entrances
        if request.party.mobility_support_required or request.walking.avoid_stairs_required:
            return ()
        position = representative_position or self._load_representative_position(place_id)
        if position is None:
            return ()
        return (
            self._representative_endpoint(
                place_id=place_id,
                position=position,
                allowed_modes=request.transport.allowed_modes,
            ),
        )

    def _load_representative_position(self, place_id: str) -> Coordinates | None:
        """active TourAPI 장소 좌표와 source fact만 읽어 standalone 이동 미리보기에 쓴다."""

        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """SELECT place.fact_id, place.name, place.address, place.category,
                          ST_Y(place.position::geometry), ST_X(place.position::geometry),
                          place.source_id, place.publication_id::text,
                          place.data_as_of, place.observed_at
                   FROM travel_read.active_place place
                   CROSS JOIN travel_read.active_service_area_boundary boundary
                   WHERE place.fact_id = %s
                     AND ST_Covers(boundary.geometry, place.position::geometry)""",
                (place_id,),
            ).fetchone()
        if row is None:
            return None
        if not self._source_is_fresh(str(row[6]), source_date=row[8], observed_at=row[9]):
            return None
        self._add_source_fact(
            str(row[0]),
            "place",
            {"name": str(row[1]), "address": str(row[2]), "content_type_id": str(row[3])},
            str(row[6]),
            str(row[7]),
            row[8],
            row[9],
        )
        return Coordinates(latitude=float(row[4]), longitude=float(row[5]))

    def _active_place_is_fresh(self, place_id: str) -> bool:
        cached = getattr(self, "_place_freshness", {}).get(place_id)
        if cached is not None:
            return cached
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """SELECT source_id, data_as_of, observed_at
                   FROM travel_read.active_place WHERE fact_id = %s""",
                (place_id,),
            ).fetchone()
        fresh = row is not None and self._source_is_fresh(
            str(row[0]), source_date=row[1], observed_at=row[2]
        )
        if not hasattr(self, "_place_freshness"):
            self._place_freshness = {}
        self._place_freshness[place_id] = fresh
        return fresh

    def _source_is_fresh(
        self,
        source_id: str,
        *,
        source_date: date | datetime | None,
        observed_at: datetime | None,
        retrieved_at: datetime | None = None,
    ) -> bool:
        source_catalog = getattr(self, "_source_catalog", load_default_source_catalog())
        try:
            source = source_catalog.require(source_id)
        except SourceNotApprovedError:
            return False
        now = getattr(self, "_now", lambda: datetime.now(UTC))
        return source.is_fresh(
            source_date=source_date,
            observed_at=observed_at,
            retrieved_at=retrieved_at or observed_at,
            now=now(),
        )

    @staticmethod
    def _fresh_enough(
        observed_at: datetime | None,
        maximum_days: int,
        *,
        source_date: date | None = None,
    ) -> bool:
        if observed_at is not None:
            value = (
                observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
            )
            age = datetime.now(UTC) - value.astimezone(UTC)
            return -timedelta(minutes=5) <= age <= timedelta(days=maximum_days)
        if source_date is None:
            return False
        age_days = (datetime.now(KST).date() - source_date).days
        return 0 <= age_days <= maximum_days

    def route(
        self,
        from_id: str,
        to_id: str,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
    ) -> VerifiedRouteOption | None:
        origins = self._entrances.get(from_id) or self._load_route_endpoints(from_id, request)
        destinations = self._entrances.get(to_id) or self._load_route_endpoints(to_id, request)
        if not origins or not destinations:
            return None
        try:
            result = self._route_planner.plan(
                origins, destinations, departure_at, strategy, request, budget
            )
        except ValueError:
            # provider 본문이나 좌표를 오류 응답에 싣지 않고 검증 불가 구간으로 닫는다.
            return None
        if result is None:
            return None
        self._facts.update({fact.fact_id: fact for fact in result.evidence_facts})
        self._sources.update({source.source_id: source for source in result.data_sources})
        return result.option

    def evidence_facts(self) -> tuple[EvidenceFact, ...]:
        return tuple(self._facts.values())

    def data_sources(self) -> tuple[DataSourceMetadata, ...]:
        return tuple(self._sources.values())

    def policy_fact_id(self, policy_key: str) -> str:
        return self._policy_fact_id(policy_key)

    def _policy_fact_id(self, policy_key: str) -> str:
        fact_id = f"policy:{self._planning_policy.policy_version}:{policy_key}"
        if fact_id not in self._facts:
            self._facts[fact_id] = EvidenceFact(
                fact_id=fact_id,
                category="planning_policy",
                value={"policy_key": policy_key},
                data_as_of=self._planning_policy.effective_from,
                retrieved_at=datetime.now(UTC),
                confidence=1,
                is_estimated=True,
                derivation=Derivation(kind="policy"),
            )
        return fact_id

    def _add_source_fact(
        self,
        fact_id: str,
        category: str,
        value: Any,
        source_id: str,
        publication_id: str | None,
        data_as_of: date | datetime,
        retrieved_at: datetime,
    ) -> None:
        self._facts[fact_id] = EvidenceFact(
            fact_id=fact_id,
            category=category,
            value=value,
            source_refs=(
                SourceRef(
                    source_id=source_id,
                    publication_id=publication_id,
                    source_fact_id=fact_id,
                ),
            ),
            data_as_of=data_as_of,
            retrieved_at=retrieved_at,
            confidence=1,
            derivation=Derivation(kind="source"),
        )
        self._add_source_metadata(source_id, publication_id)

    def _add_source_metadata(self, source_id: str, publication_id: str | None) -> None:
        if source_id in self._sources:
            return
        query = """
        SELECT source_id, provider, dataset_version, source_date, observed_at,
               published_at
        FROM travel_read.active_source_metadata
        WHERE source_id = %(source_id)s
          AND (%(publication_id)s::text IS NULL
               OR publication_id::text = %(publication_id)s::text)
        LIMIT 1
        """
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                query,
                {"source_id": source_id, "publication_id": publication_id},
            ).fetchone()
        if row is None:
            return
        is_fresh = self._source_is_fresh(
            source_id,
            source_date=row[3],
            observed_at=row[4],
            retrieved_at=row[4] or row[5],
        )
        self._sources[source_id] = DataSourceMetadata(
            source_id=str(row[0]),
            provider=str(row[1]),
            dataset_version=str(row[2]) if row[2] is not None else None,
            data_as_of=row[3],
            retrieved_at=row[4] or row[5],
            status="ACTIVE" if is_fresh else "STALE",
            attribution_text=str(row[1]),
        )

    def _assert_position_in_service_area(self, latitude: float, longitude: float) -> None:
        """사용자가 준 좌표는 active 제주 polygon이 덮는 경우에만 해석에 사용한다."""

        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """SELECT COALESCE(bool_or(ST_Covers(
                             boundary.geometry,
                             ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)
                           )), false)
                   FROM travel_read.active_service_area_boundary boundary""",
                {"latitude": latitude, "longitude": longitude},
            ).fetchone()
        if row is None or not bool(row[0]):
            raise ValueError("PLACE_OUTSIDE_SERVICE_AREA")

    def _record_active_boundary_fact(self) -> None:
        """경계 geometry 없이 판정에 사용한 fact/publication만 provenance에 추가한다."""

        if any(fact.category == "service_area_boundary" for fact in self._facts.values()):
            return
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """SELECT boundary.fact_id, boundary.boundary_id, boundary.name,
                          boundary.publication_id::text, metadata.source_date,
                          metadata.observed_at
                   FROM travel_read.active_service_area_boundary boundary
                   JOIN travel_read.active_source_metadata metadata
                     ON metadata.publication_id = boundary.publication_id
                   LIMIT 1"""
            ).fetchone()
        if row is None:
            return
        self._add_source_fact(
            str(row[0]),
            "service_area_boundary",
            {"boundary_id": str(row[1]), "name": str(row[2])},
            "spatial.jeju-boundary",
            str(row[3]),
            row[4] or row[5].date(),
            row[5],
        )

    @staticmethod
    def _stay_policy_key(category: str, detailed_category: str | None = None) -> str:
        if detailed_category == "A05020900":
            return "cafe"
        return {
            "12": "viewpoint_or_beach",
            "14": "museum_or_exhibition",
            "15": "experience_or_theme",
            "28": "experience_or_theme",
            "38": "market_or_shopping",
            "39": "restaurant",
        }.get(category, "viewpoint_or_beach")
