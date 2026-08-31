"""정확 일정 판정 요청마다 격리된 Postgres·TMAP evidence를 제공한다."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg

from jeju_trip.domain.models import (
    ActivitiesOnlyEvaluationInput,
    DataSourceMetadata,
    Derivation,
    DiscoveryPreferences,
    EvaluateJejuDayTripInput,
    EvidenceFact,
    RecommendDayTripsInput,
    SourceRef,
    Strategy,
)
from jeju_trip.infrastructure.runtime_generation_gateway import PostgresGenerationGateway
from jeju_trip.infrastructure.runtime_routing import TmapDoorRoutePlanner
from jeju_trip.planning.evaluation import RouteEvidence
from jeju_trip.planning.execution_budget import ExecutionBudget
from jeju_trip.planning.policy import PlanningPolicy

KST = ZoneInfo("Asia/Seoul")
TravelMode = Literal["walk", "bus", "taxi"]


class PostgresEvaluationEvidence:
    """한 판정 요청의 route·운영시간 fact만 메모리에 보관한다."""

    def __init__(
        self,
        runtime_dsn: str,
        route_planner: TmapDoorRoutePlanner,
        planning_policy: PlanningPolicy,
        request: EvaluateJejuDayTripInput,
    ) -> None:
        self._runtime_dsn = runtime_dsn
        self._request = request
        self._planning_policy = planning_policy
        self._gateway = PostgresGenerationGateway(runtime_dsn, route_planner, planning_policy)
        if (
            isinstance(request, ActivitiesOnlyEvaluationInput)
            and request.start_location is not None
            and request.start_location.place_id is not None
            and request.start_location.coordinates is not None
        ):
            self._gateway.register_current_endpoint(
                request.start_location.place_id,
                request.start_location.coordinates,
                request.transport.allowed_modes,
            )
        self._opening_facts: dict[str, EvidenceFact] = {}
        self._opening_windows_cache: dict[
            tuple[str, date], tuple[tuple[datetime, datetime, tuple[str, ...]], ...]
        ] = {}
        self._last_admissions: dict[tuple[str, date], tuple[datetime, tuple[str, ...]]] = {}
        self._route_facts: dict[tuple[str, str, datetime, TravelMode], RouteEvidence | None] = {}
        self._sources: dict[str, DataSourceMetadata] = {}
        self._routing_budget = ExecutionBudget.evaluation()

    def route(
        self, from_place_id: str, to_place_id: str, departure_at: datetime
    ) -> RouteEvidence | None:
        ordered = tuple(
            dict.fromkeys(
                (
                    self._request.transport.preferred_mode,
                    *self._request.transport.fallback_order,
                    *sorted(self._request.transport.allowed_modes),
                )
            )
        )
        candidates = tuple(
            candidate
            for mode in ordered
            if (candidate := self.route_for_mode(from_place_id, to_place_id, departure_at, mode))
            is not None
        )
        if not candidates:
            return None
        policy = self._request.transport.selection_policy
        if policy == "prefer_selected":
            return candidates[0]
        if policy == "fastest":
            return min(candidates, key=lambda item: item.duration_minutes)
        if policy == "lowest_cost":
            return min(candidates, key=lambda item: item.cost_krw)
        if policy == "least_walking":
            return min(candidates, key=lambda item: item.walking_minutes)
        if policy == "fewest_transfers":
            return min(candidates, key=lambda item: item.transfers)
        return min(
            candidates,
            key=lambda item: (
                item.duration_minutes
                + item.walking_minutes
                + item.cost_krw / 1000
                + item.transfers * 10
            ),
        )

    def route_for_mode(
        self,
        from_place_id: str,
        to_place_id: str,
        departure_at: datetime,
        mode: TravelMode,
    ) -> RouteEvidence | None:
        if mode not in self._request.transport.allowed_modes:
            return None
        cache_key = (from_place_id, to_place_id, departure_at, mode)
        if cache_key not in self._route_facts:
            self._route_facts[cache_key] = self._load_route_for_mode(
                from_place_id, to_place_id, departure_at, mode
            )
        return self._route_facts[cache_key]

    def _load_route_for_mode(
        self,
        from_place_id: str,
        to_place_id: str,
        departure_at: datetime,
        mode: TravelMode,
    ) -> RouteEvidence | None:
        routing_request = self._routing_request(mode)
        option = self._gateway.route(
            from_place_id,
            to_place_id,
            departure_at,
            Strategy.BALANCED,
            routing_request,
            self._routing_budget,
        )
        if option is None:
            return None
        transfer = option.transfer
        # Transfer.distance_meters는 Generate가 선택한 수단과 무관하게 공개
        # 타임라인에 보존하는 동일 거리 claim이다. 버스에서 이를 None으로
        # 버리면 시간·노선·정류장이 모두 일치해도 Evaluate가 근거 불가로
        # 판정하므로 재조회한 선택 경로의 값을 그대로 사용한다.
        distance = transfer.distance_meters
        stairs = (
            transfer.direct_walk.stairs_status if transfer.direct_walk is not None else "UNKNOWN"
        )
        first_bus_ride = transfer.bus_rides[0] if transfer.bus_rides else None
        last_bus_ride = transfer.bus_rides[-1] if transfer.bus_rides else None
        scheduled_wait = (
            max(
                0,
                int(
                    (
                        first_bus_ride.scheduled_departure_at
                        - departure_at
                        - timedelta(
                            minutes=(
                                transfer.access_walk.planned_minutes
                                if transfer.access_walk is not None
                                else 0
                            )
                        )
                    ).total_seconds()
                    // 60
                ),
            )
            if first_bus_ride is not None
            else 0
        )
        return RouteEvidence(
            mode=option.transfer.mode,
            duration_minutes=option.duration_minutes,
            distance_meters=distance,
            cost_krw=option.cost_max_krw,
            cost_min_krw=option.cost_min_krw,
            cost_max_krw=option.cost_max_krw,
            walking_minutes=option.walking_minutes,
            walking_distance_meters=option.walking_distance_meters,
            transfers=option.transfers,
            evidence_fact_ids=option.evidence_fact_ids,
            stairs_status=stairs,
            route_number=first_bus_ride.route_number if first_bus_ride is not None else None,
            provider_route_id=first_bus_ride.route_id if first_bus_ride is not None else None,
            boarding_stop_id=(
                first_bus_ride.canonical_boarding_stop_id
                if first_bus_ride is not None
                else None
            ),
            alighting_stop_id=(
                last_bus_ride.canonical_alighting_stop_id if last_bus_ride is not None else None
            ),
            scheduled_departure_at=(
                first_bus_ride.scheduled_departure_at if first_bus_ride is not None else None
            ),
            scheduled_arrival_at=(
                last_bus_ride.scheduled_arrival_at if last_bus_ride is not None else None
            ),
            scheduled_wait_minutes=scheduled_wait,
        )

    def _routing_request(self, mode: TravelMode) -> RecommendDayTripsInput:
        transport = self._request.transport.model_copy(
            update={
                "allowed_modes": {mode},
                "preferred_mode": mode,
                "fallback_order": (),
                "selection_policy": "prefer_selected",
            }
        )
        return RecommendDayTripsInput(
            trip_date=self._request.trip_date,
            timezone=self._request.timezone,
            accommodation=self._request.accommodation,
            activity_window=self._request.activity_window,
            party=self._request.party,
            transport=transport,
            walking=self._request.walking,
            rest=self._request.rest,
            discovery=DiscoveryPreferences(
                allow_additional_attractions=False,
                maximum_additional_places=0,
            ),
            food=self._request.food,
            total_budget_krw=self._request.total_budget_krw,
        )

    def opening_window(
        self, place_id: str, on_date: date
    ) -> tuple[datetime, datetime, tuple[str, ...]] | None:
        windows = self.opening_windows(place_id, on_date)
        return windows[0] if windows else None

    def opening_windows(
        self, place_id: str, on_date: date
    ) -> tuple[tuple[datetime, datetime, tuple[str, ...]], ...]:
        cache_key = (place_id, on_date)
        if cache_key in self._opening_windows_cache:
            return self._opening_windows_cache[cache_key]
        with psycopg.connect(self._runtime_dsn) as connection:
            exception = connection.execute(
                """SELECT value.fact_id, value.exception_type, value.opens_minute,
                          value.closes_minute, value.closes_day_offset,
                          value.publication_id::text, metadata.source_id,
                          metadata.provider, metadata.dataset_version, metadata.source_date,
                          metadata.observed_at
                   FROM (
                     SELECT exact.fact_id, exact.exception_type, exact.opens_minute,
                            exact.closes_minute, exact.closes_day_offset,
                            exact.publication_id, 0 AS priority
                     FROM travel_read.active_place_schedule_exception exact
                     WHERE exact.place_fact_id = %s AND exact.exception_date = %s
                     UNION ALL
                     SELECT weekly.fact_id, 'CLOSED'::text, NULL::smallint,
                            NULL::smallint, 0::smallint, weekly.publication_id, 1 AS priority
                     FROM travel_read.active_place_weekly_closure weekly
                     WHERE weekly.place_fact_id = %s AND weekly.service_day = %s
                       AND (weekly.valid_from IS NULL OR weekly.valid_from <= %s)
                       AND (weekly.valid_to IS NULL OR weekly.valid_to >= %s)
                   ) value
                   JOIN travel_read.active_source_metadata metadata
                     ON metadata.publication_id = value.publication_id
                   WHERE (
                       (metadata.observed_at IS NOT NULL
                        AND metadata.observed_at >= now() - interval '8 days'
                        AND metadata.observed_at <= now() + interval '5 minutes')
                       OR
                       (metadata.observed_at IS NULL
                        AND metadata.source_date BETWEEN
                          (now() AT TIME ZONE 'Asia/Seoul')::date - 8
                          AND (now() AT TIME ZONE 'Asia/Seoul')::date)
                     )
                   ORDER BY value.priority
                   LIMIT 1""",
                (
                    place_id,
                    on_date,
                    place_id,
                    on_date.isoweekday(),
                    on_date,
                    on_date,
                ),
            ).fetchone()
            rules = connection.execute(
                """SELECT value.fact_id, opens_minute, closes_minute, closes_day_offset,
                          value.publication_id::text, metadata.source_id, metadata.provider,
                          metadata.dataset_version, metadata.source_date, metadata.observed_at,
                          last_admission_minute, period_kind
                   FROM travel_read.active_place_opening_rule value
                   JOIN travel_read.active_source_metadata metadata
                     ON metadata.publication_id = value.publication_id
                   WHERE place_fact_id = %s AND period_kind IN ('OPEN', 'BREAK')
                     AND (
                       (metadata.observed_at IS NOT NULL
                        AND metadata.observed_at >= now() - interval '8 days'
                        AND metadata.observed_at <= now() + interval '5 minutes')
                       OR
                       (metadata.observed_at IS NULL
                        AND metadata.source_date BETWEEN
                          (now() AT TIME ZONE 'Asia/Seoul')::date - 8
                          AND (now() AT TIME ZONE 'Asia/Seoul')::date)
                     )
                     AND normalization_status = 'VERIFIED'
                     AND (service_day IS NULL OR service_day = %s)
                     AND (valid_from IS NULL OR valid_from <= %s)
                     AND (valid_to IS NULL OR valid_to >= %s)
                   ORDER BY period_kind DESC, opens_minute""",
                (place_id, on_date.isoweekday(), on_date, on_date),
            ).fetchall()
        day_start = datetime.combine(on_date, datetime.min.time(), tzinfo=KST)
        open_rows = tuple(rule for rule in rules if rule[11] == "OPEN")
        break_rows = tuple(rule for rule in rules if rule[11] == "BREAK")
        if exception is not None:
            self._record_opening_fact(exception, on_date, exception_layout=True)
            if exception[1] == "CLOSED":
                result = ((day_start, day_start, (str(exception[0]),)),)
                self._opening_windows_cache[cache_key] = result
                return result
            for break_rule in break_rows:
                self._record_opening_fact(break_rule, on_date, exception_layout=False)
            result_list = [
                (
                    day_start + timedelta(minutes=int(exception[2])),
                    day_start + timedelta(days=int(exception[4]), minutes=int(exception[3])),
                    (str(exception[0]),),
                )
            ]
            for break_rule in break_rows:
                break_start = day_start + timedelta(minutes=int(break_rule[1]))
                break_end = day_start + timedelta(
                    days=int(break_rule[3]), minutes=int(break_rule[2])
                )
                next_segments = []
                for starts_at, ends_at, fact_ids in result_list:
                    if break_end <= starts_at or break_start >= ends_at:
                        next_segments.append((starts_at, ends_at, fact_ids))
                        continue
                    split_fact_ids = (*fact_ids, str(break_rule[0]))
                    if starts_at < break_start:
                        next_segments.append((starts_at, break_start, split_fact_ids))
                    if break_end < ends_at:
                        next_segments.append((break_end, ends_at, split_fact_ids))
                result_list = next_segments
            result = tuple(result_list) or (
                (
                    day_start,
                    day_start,
                    tuple(
                        dict.fromkeys(
                            (str(exception[0]), *(str(row[0]) for row in break_rows))
                        )
                    ),
                ),
            )
            self._opening_windows_cache[cache_key] = result
            return result
        for rule in rules:
            self._record_opening_fact(rule, on_date, exception_layout=False)
        result_list: list[tuple[datetime, datetime, tuple[str, ...]]] = []
        for rule in open_rows:
            if rule[10] is not None:
                self._last_admissions[(place_id, on_date)] = (
                    day_start + timedelta(minutes=int(rule[10])),
                    (str(rule[0]),),
                )
            segments = [
                (
                    day_start + timedelta(minutes=int(rule[1])),
                    day_start + timedelta(days=int(rule[3]), minutes=int(rule[2])),
                    (str(rule[0]),),
                )
            ]
            for break_rule in break_rows:
                break_start = day_start + timedelta(minutes=int(break_rule[1]))
                break_end = day_start + timedelta(
                    days=int(break_rule[3]), minutes=int(break_rule[2])
                )
                next_segments: list[tuple[datetime, datetime, tuple[str, ...]]] = []
                for starts_at, ends_at, fact_ids in segments:
                    if break_end <= starts_at or break_start >= ends_at:
                        next_segments.append((starts_at, ends_at, fact_ids))
                        continue
                    split_fact_ids = (*fact_ids, str(break_rule[0]))
                    if starts_at < break_start:
                        next_segments.append((starts_at, break_start, split_fact_ids))
                    if break_end < ends_at:
                        next_segments.append((break_end, ends_at, split_fact_ids))
                segments = next_segments
            result_list.extend(segments)
        result = tuple(sorted(result_list, key=lambda item: (item[0], item[1], item[2])))
        if not result and rules:
            result = (
                (
                    day_start,
                    day_start,
                    tuple(dict.fromkeys(str(rule[0]) for rule in rules)),
                ),
            )
        self._opening_windows_cache[cache_key] = result
        return result

    def last_admission_at(
        self, place_id: str, on_date: date
    ) -> tuple[datetime, tuple[str, ...]] | None:
        """운영시간 조회에서 함께 확보한 마지막 입장 시각만 반환한다."""

        return self._last_admissions.get((place_id, on_date))

    def dietary_safety(
        self,
        place_id: str,
        allergens: tuple[str, ...],
        excluded_foods: tuple[str, ...],
        on_date: date,
    ) -> tuple[bool, tuple[str, ...]] | None:
        """생성과 동일한 active 식이 fact 조회를 판정에서도 재사용한다."""

        return self._gateway.dietary_safety(
            place_id, allergens, excluded_foods, on_date
        )

    def _record_opening_fact(self, row, on_date: date, *, exception_layout: bool) -> None:
        publication_index = 5 if exception_layout else 4
        source_index = publication_index + 1
        provider_index = publication_index + 2
        dataset_index = publication_index + 3
        source_date_index = publication_index + 4
        observed_index = publication_index + 5
        fact_id = str(row[0])
        source_id = str(row[source_index])
        self._opening_facts[fact_id] = EvidenceFact(
            fact_id=fact_id,
            category="opening_hours",
            value={"trip_date": on_date.isoformat()},
            source_refs=(
                SourceRef(
                    source_id=source_id,
                    publication_id=str(row[publication_index]),
                    source_fact_id=fact_id,
                ),
            ),
            data_as_of=row[source_date_index] or on_date,
            retrieved_at=row[observed_index] or datetime.now(UTC),
            confidence=1,
            derivation=Derivation(kind="source"),
        )
        self._sources[source_id] = DataSourceMetadata(
            source_id=source_id,
            provider=str(row[provider_index]),
            dataset_version=(str(row[dataset_index]) if row[dataset_index] is not None else None),
            data_as_of=row[source_date_index],
            retrieved_at=row[observed_index] or datetime.now(UTC),
            status="ACTIVE",
            attribution_text=str(row[provider_index]),
        )

    def evidence_facts(self) -> tuple[EvidenceFact, ...]:
        facts = {fact.fact_id: fact for fact in self._gateway.evidence_facts()}
        facts.update(self._opening_facts)
        return tuple(facts.values())

    def data_sources(self) -> tuple[DataSourceMetadata, ...]:
        sources = {source.source_id: source for source in self._gateway.data_sources()}
        sources.update(self._sources)
        return tuple(sources.values())


class PostgresEvaluationEvidenceFactory:
    """서비스에 요청별 evidence 객체를 제공해 사용자 일정 간 상태 공유를 막는다."""

    def __init__(
        self,
        runtime_dsn: str,
        route_planner: TmapDoorRoutePlanner,
        planning_policy: PlanningPolicy,
    ) -> None:
        self._runtime_dsn = runtime_dsn
        self._route_planner = route_planner
        self._planning_policy = planning_policy

    def __call__(self, request: EvaluateJejuDayTripInput) -> PostgresEvaluationEvidence:
        return PostgresEvaluationEvidence(
            self._runtime_dsn,
            self._route_planner,
            self._planning_policy,
            request,
        )
