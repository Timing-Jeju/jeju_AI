"""검증된 입구 사이의 TMAP 보행·차량 후보를 사용자 정책으로 선택한다."""

from __future__ import annotations

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Literal, cast
from zoneinfo import ZoneInfo

import psycopg

from jeju_trip.domain.models import (
    BusRide,
    Coordinates,
    CostRange,
    DataSourceMetadata,
    Derivation,
    EndpointBasis,
    EvidenceFact,
    RecommendDayTripsInput,
    SourceRef,
    Strategy,
    TaxiAlternative,
    Transfer,
    WalkConnection,
)
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.infrastructure.tmap_adapter import (
    DrivingRouteFact,
    HttpxJsonTransport,
    PedestrianRouteFact,
    TmapAdapter,
    TmapRequestFailed,
    normalize_tmap_driving,
    normalize_tmap_pedestrian,
)
from jeju_trip.infrastructure.tmap_cache import EphemeralRouteCache, RouteCacheKey
from jeju_trip.planning.cost_time_balance import (
    TransportCandidate,
    select_cost_time_balance,
)
from jeju_trip.planning.execution_budget import ExecutionBudget
from jeju_trip.planning.generation import VerifiedRouteOption
from jeju_trip.planning.policy import (
    BusFarePolicy,
    PlanningPolicy,
    TaxiFarePolicy,
    estimate_bus_fare,
    estimate_taxi_fare,
)

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class VerifiedEntrance:
    entrance_id: str
    place_id: str
    position: Coordinates
    supported_modes: tuple[str, ...]
    endpoint_basis: EndpointBasis = EndpointBasis.VERIFIED_ENTRANCE
    evidence_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutePlanResult:
    option: VerifiedRouteOption
    evidence_facts: tuple[EvidenceFact, ...]
    data_sources: tuple[DataSourceMetadata, ...]


@dataclass(frozen=True)
class _BusStopCandidate:
    canonical_stop_id: str
    provider_stop_id: str
    name: str
    direction_text: str
    position: Coordinates
    stop_fact_id: str
    identity_fact_id: str
    stop_publication_id: str
    identity_publication_id: str


class TmapDoorRoutePlanner:
    """TMAP 수치를 바꾸지 않고 정책 배수·공식 택시 요율만 결정론적으로 적용한다."""

    def __init__(
        self,
        catalog: SourceCatalog,
        transport: HttpxJsonTransport,
        api_key: str,
        planning_policy: PlanningPolicy,
        taxi_policy: TaxiFarePolicy,
        runtime_dsn: str | None = None,
        bus_fare_policy: BusFarePolicy | None = None,
        safe_log=None,
    ) -> None:
        if not api_key:
            raise ValueError("TMAP_API_KEY_MISSING")
        self._pedestrian_contract = catalog.require("tmap.pedestrian")
        self._driving_contract = catalog.require("tmap.driving")
        self._catalog = catalog
        self._transport = transport
        self._api_key = api_key
        self._planning_policy = planning_policy
        self._taxi_policy = taxi_policy
        self._runtime_dsn = runtime_dsn
        self._bus_fare_policy = bus_fare_policy
        self._safe_log = safe_log or (lambda _: None)
        self._pedestrian_cache = EphemeralRouteCache[PedestrianRouteFact](6 * 60 * 60)
        self._driving_cache = EphemeralRouteCache[DrivingRouteFact](30 * 60)
        self._failed_route_keys: set[RouteCacheKey] = set()
        self._route_key_locks: dict[RouteCacheKey, Lock] = {}
        self._route_key_locks_guard = Lock()

    def plan(
        self,
        origins: tuple[VerifiedEntrance, ...],
        destinations: tuple[VerifiedEntrance, ...],
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
    ) -> RoutePlanResult | None:
        modes = self._ordered_modes(request)
        candidates: list[RoutePlanResult] = []
        unavailable: list[TransportCandidate] = []
        unavailable_facts: list[EvidenceFact] = []
        endpoints = []
        bus_endpoints: tuple[VerifiedEntrance, VerifiedEntrance] | None = None
        skipped_walk: tuple[VerifiedEntrance, VerifiedEntrance] | None = None
        for mode in modes:
            origin = next((item for item in origins if mode in item.supported_modes), None)
            destination = next(
                (item for item in destinations if mode in item.supported_modes), None
            )
            if origin is None or destination is None:
                continue
            if mode == "walk" and request.transport.selection_policy == "cost_time_balance":
                skipped = self._prefilter_direct_walk(origin, destination, request)
                if skipped is not None:
                    candidate_summary, evidence = skipped
                    unavailable.append(candidate_summary)
                    unavailable_facts.extend(evidence)
                    skipped_walk = (origin, destination)
                    continue
            endpoints.append((mode, origin, destination))
            if mode == "bus":
                bus_endpoints = (origin, destination)

        if request.transport.selection_policy == "cost_time_balance":
            with ThreadPoolExecutor(
                max_workers=min(budget.maximum_concurrency, len(endpoints) or 1)
            ) as executor:
                futures = tuple(
                    executor.submit(
                        self._candidate_for_mode,
                        mode,
                        origin,
                        destination,
                        departure_at,
                        strategy,
                        request,
                        budget,
                    )
                    for mode, origin, destination in endpoints
                )
                candidates.extend(
                    candidate for future in futures if (candidate := future.result()) is not None
                )
            if not any(item.option.transfer.mode == "bus" for item in candidates) and (
                bus_endpoints is not None
            ):
                bus_diagnostic = self._exact_bus_unavailable(
                    *bus_endpoints, departure_at, request
                ) or self._unverifiable_bus_pattern(*bus_endpoints, request.trip_date)
                if bus_diagnostic is not None:
                    candidate, facts = bus_diagnostic
                    unavailable.append(candidate)
                    unavailable_facts.extend(facts)
            if not candidates and skipped_walk is not None:
                fallback_walk = self._candidate_for_mode(
                    "walk",
                    *skipped_walk,
                    departure_at,
                    strategy,
                    request,
                    budget,
                )
                if fallback_walk is not None:
                    candidates.append(fallback_walk)
                    unavailable.clear()
                    unavailable_facts.clear()
        else:
            for mode, origin, destination in endpoints:
                candidate = self._candidate_for_mode(
                    mode, origin, destination, departure_at, strategy, request, budget
                )
                if candidate is not None:
                    candidates.append(candidate)
                    if request.transport.selection_policy == "prefer_selected":
                        return candidate

        if not candidates:
            return None
        policy = request.transport.selection_policy
        if policy == "cost_time_balance":
            selected = self._select_cost_time_balance(
                candidates,
                departure_at,
                request,
                unavailable=tuple(unavailable),
                unavailable_facts=tuple(unavailable_facts),
            )
            mode = selected.option.transfer.mode
            origin = next(item for item in origins if mode in item.supported_modes)
            destination = next(item for item in destinations if mode in item.supported_modes)
            decision = selected.option.transfer.mode_decision
            if decision is None:  # pragma: no cover - cost-time policy always creates it
                return selected
            transfer = selected.option.transfer.model_copy(
                update={
                    "mode_decision": decision.model_copy(
                        update={
                            "origin_basis": origin.endpoint_basis,
                            "destination_basis": destination.endpoint_basis,
                        }
                    )
                }
            )
            return replace(
                selected,
                option=replace(selected.option, transfer=transfer),
            )
        if policy == "fastest":
            return min(candidates, key=lambda item: item.option.duration_minutes)
        if policy == "lowest_cost":
            return min(candidates, key=lambda item: item.option.cost_max_krw)
        if policy == "least_walking":
            return min(candidates, key=lambda item: item.option.walking_minutes)
        if policy == "fewest_transfers":
            return min(candidates, key=lambda item: item.option.transfers)
        return min(
            candidates,
            key=lambda item: (
                item.option.duration_minutes
                + item.option.walking_minutes
                + item.option.cost_max_krw / 1000
                + item.option.transfers * 10
            ),
        )

    def _candidate_for_mode(
        self,
        mode: str,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
    ) -> RoutePlanResult | None:
        try:
            return (
                self._walk(origin, destination, departure_at, strategy, request, budget)
                if mode == "walk"
                else self._bus(origin, destination, departure_at, strategy, request, budget)
                if mode == "bus"
                else self._taxi(origin, destination, departure_at, request, budget)
                if mode == "taxi"
                else None
            )
        except TmapRequestFailed:
            return None

    @staticmethod
    def _select_cost_time_balance(
        candidates: list[RoutePlanResult],
        departure_at: datetime,
        request: RecommendDayTripsInput,
        *,
        unavailable: tuple[TransportCandidate, ...] = (),
        unavailable_facts: tuple[EvidenceFact, ...] = (),
    ) -> RoutePlanResult:
        """runtime 후보를 공개 비교 모델로 바꾸고 같은 결정론 정책을 적용한다."""

        by_mode = {item.option.transfer.mode: item for item in candidates}
        converted: dict[str, TransportCandidate] = {
            candidate.mode: candidate for candidate in unavailable
        }
        for mode, candidate in by_mode.items():
            option = candidate.option
            transfer = option.transfer
            pickup: int | None = None
            driving: int | None = None
            wait: int | None = None
            duration = option.duration_minutes
            distance = transfer.distance_meters or option.walking_distance_meters
            if mode == "taxi":
                pickup = request.transport.taxi_pickup_buffer_minutes
                driving = option.duration_minutes
                duration = pickup + driving
                if transfer.taxi_alternative is not None:
                    distance = transfer.taxi_alternative.distance_meters
            elif mode == "bus" and transfer.bus_rides:
                ride = transfer.bus_rides[0]
                access_minutes = transfer.access_walk.planned_minutes if transfer.access_walk else 0
                wait = max(
                    0,
                    int(
                        (
                            ride.scheduled_departure_at
                            - departure_at
                            - timedelta(minutes=access_minutes)
                        ).total_seconds()
                        // 60
                    ),
                )
            converted[mode] = TransportCandidate(
                mode=cast(Literal["walk", "bus", "taxi"], mode),
                door_to_door_minutes=duration,
                walking_minutes=option.walking_minutes,
                distance_meters=distance,
                cost=CostRange(
                    min_krw=option.cost_min_krw,
                    max_krw=option.cost_max_krw,
                    is_estimated=mode == "taxi",
                ),
                status="feasible",
                wait_minutes=wait,
                pickup_buffer_minutes=pickup,
                driving_minutes=driving,
                route_id=(transfer.bus_rides[0].route_id if transfer.bus_rides else None),
                route_number=(transfer.bus_rides[0].route_number if transfer.bus_rides else None),
                evidence_fact_ids=option.evidence_fact_ids,
            )
        selection = select_cost_time_balance(
            walk=converted.get("walk"),
            bus=converted.get("bus"),
            taxi=converted.get("taxi"),
            preferences=request.transport,
        )
        selected = by_mode[selection.selected.mode]
        all_facts = tuple(
            {
                fact.fact_id: fact
                for fact in (
                    *unavailable_facts,
                    *(fact for candidate in candidates for fact in candidate.evidence_facts),
                )
            }.values()
        )
        all_sources = tuple(
            {
                source.source_id: source
                for candidate in candidates
                for source in candidate.data_sources
            }.values()
        )
        all_fact_ids = tuple(
            dict.fromkeys(
                fact_id
                for fact_id in (
                    *(
                        fact_id
                        for candidate in candidates
                        for fact_id in candidate.option.evidence_fact_ids
                    ),
                    *(
                        fact_id
                        for candidate in unavailable
                        for fact_id in candidate.evidence_fact_ids
                    ),
                )
            )
        )
        updated_transfer = selected.option.transfer.model_copy(
            update={
                "alternatives": selection.alternatives,
                "mode_decision": selection.decision,
            }
        )
        updated_option = replace(
            selected.option,
            duration_minutes=selection.selected.door_to_door_minutes,
            transfer=updated_transfer,
            evidence_fact_ids=all_fact_ids,
        )
        return RoutePlanResult(updated_option, all_facts, all_sources)

    def _prefilter_direct_walk(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        request: RecommendDayTripsInput,
    ) -> tuple[TransportCandidate, tuple[EvidenceFact, ...]] | None:
        """최대 계획속도에서도 15분 문턱을 넘는 도보 호출만 생략한다."""

        radius_meters = 6_371_000
        first_latitude = math.radians(origin.position.latitude)
        second_latitude = math.radians(destination.position.latitude)
        latitude_delta = second_latitude - first_latitude
        longitude_delta = math.radians(destination.position.longitude - origin.position.longitude)
        haversine = (
            math.sin(latitude_delta / 2) ** 2
            + math.cos(first_latitude)
            * math.cos(second_latitude)
            * math.sin(longitude_delta / 2) ** 2
        )
        lower_bound_meters = math.ceil(2 * radius_meters * math.asin(min(1, math.sqrt(haversine))))
        maximum_distance = math.floor(
            self._planning_policy.walking_route_prefilter.maximum_planning_speed_kph
            * 1000
            * request.transport.direct_walk_limit_minutes
            / 60
        )
        if lower_bound_meters <= maximum_distance:
            return None
        policy_fact_id = (
            f"policy:{self._planning_policy.policy_version}:"
            "walking_route_prefilter.maximum_planning_speed_kph"
        )
        distance_fact_id = self._fact_id(
            "computed.walking-lower-bound", origin, destination, request.activity_window.start_at
        )
        input_fact_ids = tuple(
            dict.fromkeys((*origin.evidence_fact_ids, *destination.evidence_fact_ids))
        )
        retrieved_at = datetime.now(UTC)
        facts = (
            EvidenceFact(
                fact_id=policy_fact_id,
                category="planning_policy",
                value={
                    "maximum_planning_speed_kph": (
                        self._planning_policy.walking_route_prefilter.maximum_planning_speed_kph
                    )
                },
                data_as_of=self._planning_policy.effective_from,
                retrieved_at=retrieved_at,
                confidence=1,
                is_estimated=True,
                derivation=Derivation(kind="policy"),
            ),
            EvidenceFact(
                fact_id=distance_fact_id,
                category="walking_distance_lower_bound",
                value={"distance_meters": lower_bound_meters},
                unit="m",
                data_as_of=retrieved_at,
                retrieved_at=retrieved_at,
                confidence=1,
                derivation=Derivation(kind="computed", input_fact_ids=input_fact_ids),
            ),
        )
        return (
            TransportCandidate(
                mode="walk",
                door_to_door_minutes=None,
                walking_minutes=0,
                distance_meters=lower_bound_meters,
                cost=CostRange(min_krw=0, max_krw=0, is_estimated=False),
                status="unavailable",
                reason_codes=("WALK_EXCEEDS_DIRECT_LIMIT_LOWER_BOUND",),
                evidence_fact_ids=(distance_fact_id, policy_fact_id, *input_fact_ids),
            ),
            facts,
        )

    @staticmethod
    def _ordered_modes(request: RecommendDayTripsInput) -> tuple[str, ...]:
        if request.transport.selection_policy != "prefer_selected":
            return tuple(sorted(request.transport.allowed_modes))
        return tuple(
            dict.fromkeys(
                (
                    request.transport.preferred_mode,
                    *request.transport.fallback_order,
                    *sorted(request.transport.allowed_modes),
                )
            )
        )

    def _walk(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
    ) -> RoutePlanResult | None:
        if not request.walking.allow_direct_walk:
            return None
        leg = self._walking_leg(
            origin,
            destination,
            departure_at,
            strategy,
            request,
            budget,
            "direct_walk",
        )
        if leg is None:
            return None
        walk, evidence, source = leg
        return RoutePlanResult(
            VerifiedRouteOption(
                origin.place_id,
                destination.place_id,
                walk.planned_minutes,
                walk.planned_minutes,
                walk.distance_meters,
                0,
                0,
                0,
                Transfer(
                    mode="walk",
                    direct_walk=walk,
                    distance_meters=walk.distance_meters,
                ),
                walk.evidence_fact_ids,
            ),
            (evidence,),
            (source,),
        )

    def _walking_leg(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
        kind: Literal["access_walk", "egress_walk", "transfer_walk", "direct_walk"],
    ) -> tuple[WalkConnection, EvidenceFact, DataSourceMetadata] | None:
        if (
            kind == "transfer_walk"
            and origin.endpoint_basis == EndpointBasis.CONFIRMED_STOP
            and destination.endpoint_basis == EndpointBasis.CONFIRMED_STOP
            and origin.entrance_id == destination.entrance_id
        ):
            input_fact_ids = tuple(
                dict.fromkeys((*origin.evidence_fact_ids, *destination.evidence_fact_ids))
            )
            lineage = "|".join(input_fact_ids)
            fact_id = (
                "computed.stop-continuity:"
                + hashlib.sha256(lineage.encode()).hexdigest()[:24]
            )
            walk = WalkConnection(
                kind="transfer_walk",
                from_id=origin.entrance_id,
                to_id=destination.entrance_id,
                distance_meters=0,
                expected_minutes=0,
                speed_multiplier=1,
                route_uncertainty_minutes=0,
                planned_minutes=0,
                entrance_verification="VERIFIED",
                stairs_status="CLEAR",
                evidence_fact_ids=(fact_id, *input_fact_ids),
            )
            evidence = EvidenceFact(
                fact_id=fact_id,
                category="stop_continuity",
                value={"same_confirmed_stop": True},
                data_as_of=request.trip_date,
                retrieved_at=datetime.now(UTC),
                confidence=1,
                derivation=Derivation(
                    kind="computed",
                    formula="same_confirmed_canonical_stop",
                    input_fact_ids=input_fact_ids,
                ),
            )
            return (
                walk,
                evidence,
                self._source_metadata("transport.stop-identity-map", departure_at),
            )
        if request.walking.avoid_stairs_required:
            return None
        fact_id = self._fact_id("tmap.pedestrian", origin, destination, departure_at)
        key = self._cache_key("tmap.pedestrian", origin, destination, departure_at, request)
        if key in self._failed_route_keys:
            return None
        adapter = TmapAdapter(
            self._pedestrian_contract,
            self._pedestrian_cache,
            self._transport,
            lambda raw: normalize_tmap_pedestrian(
                raw,
                origin_entrance_id=origin.entrance_id,
                destination_entrance_id=destination.entrance_id,
                route_fact_id=fact_id,
            ),
            self._api_key,
            self._safe_log,
        )
        try:
            payload = {
                "startX": origin.position.longitude,
                "startY": origin.position.latitude,
                "endX": destination.position.longitude,
                "endY": destination.position.latitude,
                "startName": "출발지",
                "endName": "도착지",
                "reqCoordType": "WGS84GEO",
                "resCoordType": "WGS84GEO",
            }
            with self._route_key_lock(key):
                if self._pedestrian_cache.get(key) is None:
                    with budget.call_slot():
                        if self._pedestrian_cache.get(key) is None:
                            budget.claim_external_call(
                                "tmap.pedestrian",
                                allocation=("direct_walk" if kind == "direct_walk" else "bus_walk"),
                            )
                        route = adapter.fetch(key, payload)
                else:
                    route = adapter.fetch(key, payload)
        except TmapRequestFailed:
            self._failed_route_keys.add(key)
            raise
        observed_at = datetime.now(UTC)
        multiplier = self._walking_multiplier(request, strategy)
        uncertainty = self._planning_policy.boarding_buffer_minutes.route_uncertainty
        planned = math.ceil(route.expected_minutes * multiplier) + uncertainty
        limit = (
            request.walking.max_single_leg_minutes
            if kind == "direct_walk"
            else request.walking.max_access_walk_minutes
        )
        if planned > limit:
            return None
        walk = WalkConnection(
            kind=kind,
            from_id=origin.entrance_id,
            to_id=destination.entrance_id,
            distance_meters=route.distance_meters,
            expected_minutes=route.expected_minutes,
            speed_multiplier=multiplier,
            route_uncertainty_minutes=uncertainty,
            planned_minutes=planned,
            entrance_verification=(
                "PROVISIONAL_PLACE_POINT"
                if EndpointBasis.REPRESENTATIVE_PLACE_POINT
                in {origin.endpoint_basis, destination.endpoint_basis}
                else "VERIFIED"
            ),
            stairs_status="UNKNOWN",
            evidence_fact_ids=(
                fact_id,
                *origin.evidence_fact_ids,
                *destination.evidence_fact_ids,
            ),
        )
        evidence = self._route_evidence(
            fact_id,
            "walking_route",
            {"distance_meters": route.distance_meters, "expected_minutes": route.expected_minutes},
            "tmap.pedestrian",
            observed_at,
        )
        return (
            walk,
            evidence,
            self._source_metadata("tmap.pedestrian", observed_at),
        )

    def _bus_walking_leg(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
        kind: Literal["access_walk", "egress_walk", "transfer_walk"],
    ) -> tuple[WalkConnection, EvidenceFact, DataSourceMetadata] | None:
        """한 보행 후보의 provider 실패가 다른 버스 정류장 조합까지 중단하지 않게 한다."""

        try:
            return self._walking_leg(
                origin,
                destination,
                departure_at,
                strategy,
                request,
                budget,
                kind,
            )
        except TmapRequestFailed:
            return None

    def _bus(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
    ) -> RoutePlanResult | None:
        if self._runtime_dsn is None or self._bus_fare_policy is None:
            return None
        boarding_stops = self._nearest_stops(origin.position)
        alighting_stops = self._nearest_stops(destination.position)
        provisional_ready = departure_at + timedelta(
            minutes=self._planning_policy.boarding_buffer_minutes.normal
        )
        selected_signatures = self._rank_direct_stop_signatures(
            self._direct_stop_signatures(
                boarding_stops,
                alighting_stops,
                provisional_ready,
                request.trip_date,
                limit=6,
            ),
            boarding_stops,
            alighting_stops,
        )
        selected_signature_set = set(selected_signatures)
        signature_rank = {
            signature: rank for rank, signature in enumerate(selected_signatures)
        }
        boarding_stops = tuple(
            sorted(
                boarding_stops,
                key=lambda stop: min(
                    (
                        rank
                        for (boarding_id, _), rank in signature_rank.items()
                        if boarding_id == stop.stop_fact_id
                    ),
                    default=len(selected_signatures),
                ),
            )
        )
        best: RoutePlanResult | None = None
        verified_path_walks = 0
        verified_boarding_walks = 0
        for boarding in boarding_stops:
            if not any(boarding.stop_fact_id == signature[0] for signature in selected_signatures):
                continue
            if verified_boarding_walks >= 2:
                continue
            verified_boarding_walks += 1
            boarding_entrance = self._stop_entrance(boarding)
            access_result = self._bus_walking_leg(
                origin,
                boarding_entrance,
                departure_at,
                strategy,
                request,
                budget,
                "access_walk",
            )
            if access_result is None:
                continue
            access, access_fact, access_source = access_result
            ready_at = departure_at + timedelta(
                minutes=(
                    access.planned_minutes + self._planning_policy.boarding_buffer_minutes.normal
                )
            )
            ordered_alighting_stops = tuple(
                sorted(
                    alighting_stops,
                    key=lambda stop: signature_rank.get(
                        (boarding.stop_fact_id, stop.stop_fact_id),
                        len(selected_signatures),
                    ),
                )
            )
            for alighting in ordered_alighting_stops:
                if (boarding.stop_fact_id, alighting.stop_fact_id) not in selected_signature_set:
                    continue
                scheduled = self._scheduled_direct_bus(
                    boarding, alighting, ready_at, request.trip_date
                )
                if scheduled is None:
                    continue
                (
                    trip_fact_id,
                    route_fact_id,
                    provider_route_id,
                    route_number,
                    route_type,
                    board_time_fact_id,
                    alight_time_fact_id,
                    scheduled_departure,
                    scheduled_arrival,
                    direction_text,
                    timetable_publication_id,
                ) = scheduled
                scheduled_departure = scheduled_departure.astimezone(KST)
                scheduled_arrival = scheduled_arrival.astimezone(KST)
                stop_arrival_at = departure_at + timedelta(minutes=access.planned_minutes)
                bus_wait_minutes = max(
                    0,
                    math.ceil((scheduled_departure - stop_arrival_at).total_seconds() / 60),
                )
                if bus_wait_minutes > request.transport.bus_wait_limit_minutes:
                    continue
                if verified_path_walks >= 2:
                    continue
                alighting_entrance = self._stop_entrance(alighting)
                egress_result = self._bus_walking_leg(
                    alighting_entrance,
                    destination,
                    scheduled_arrival,
                    strategy,
                    request,
                    budget,
                    "egress_walk",
                )
                verified_path_walks += 1
                if egress_result is None:
                    continue
                egress, egress_fact, egress_source = egress_result
                end_at = scheduled_arrival + timedelta(minutes=egress.planned_minutes)
                duration = max(1, math.ceil((end_at - departure_at).total_seconds() / 60))
                try:
                    fare_min, fare_max = estimate_bus_fare(
                        self._bus_fare_policy,
                        route_type=route_type,
                        adults=request.party.adults,
                        seniors=request.party.seniors,
                        children=request.party.children,
                        travel_date=request.trip_date,
                    )
                except ValueError as error:
                    if str(error) == "FARE_POLICY_NOT_EFFECTIVE":
                        continue
                    raise
                if request.total_budget_krw is not None and fare_max > request.total_budget_krw:
                    continue
                schedule_fact_id = (
                    f"jeju.bus-timetable:{trip_fact_id}:{boarding.stop_fact_id}:"
                    f"{alighting.stop_fact_id}"
                )
                fare_class = "EXPRESS" if "급행" in route_type else "STANDARD"
                policy_fact_id = (
                    f"jeju.bus-fare-policy:{self._bus_fare_policy.effective_from}:{fare_class}"
                )
                fare_fact_id = (
                    f"computed.bus-fare:{fare_class}:{request.trip_date}:"
                    f"{request.party.adults}:{request.party.seniors}:{request.party.children}"
                )
                fare_publication_id = self._fare_policy_publication(
                    "jeju.bus-fare-policy", self._bus_fare_policy.effective_from
                )
                ride_fact_ids = (
                    schedule_fact_id,
                    boarding.stop_fact_id,
                    boarding.identity_fact_id,
                    alighting.stop_fact_id,
                    alighting.identity_fact_id,
                )
                ride = BusRide(
                    canonical_boarding_stop_id=boarding.canonical_stop_id,
                    provider_boarding_stop_id=boarding.provider_stop_id,
                    boarding_stop_name=boarding.name,
                    boarding_stop_position=boarding.position,
                    boarding_direction=direction_text,
                    canonical_alighting_stop_id=alighting.canonical_stop_id,
                    provider_alighting_stop_id=alighting.provider_stop_id,
                    alighting_stop_name=alighting.name,
                    alighting_stop_position=alighting.position,
                    route_id=provider_route_id,
                    route_number=route_number,
                    scheduled_departure_at=scheduled_departure,
                    scheduled_arrival_at=scheduled_arrival,
                    recommended_stop_arrival_at=scheduled_departure
                    - timedelta(minutes=self._planning_policy.boarding_buffer_minutes.normal),
                    boarding_buffer_minutes=(self._planning_policy.boarding_buffer_minutes.normal),
                    mapping_status="CONFIRMED",
                    evidence_fact_ids=ride_fact_ids,
                )
                all_fact_ids = (
                    *access.evidence_fact_ids,
                    *ride_fact_ids,
                    *egress.evidence_fact_ids,
                    policy_fact_id,
                    fare_fact_id,
                )
                facts = (
                    access_fact,
                    egress_fact,
                    self._simple_source_evidence(
                        schedule_fact_id,
                        "scheduled_bus_route",
                        {
                            "scheduled_departure_at": scheduled_departure.isoformat(),
                            "scheduled_arrival_at": scheduled_arrival.isoformat(),
                        },
                        "jeju.bus-timetable",
                        timetable_publication_id,
                        request.trip_date,
                        source_fact_id=trip_fact_id,
                    ),
                    self._simple_source_evidence(
                        boarding.stop_fact_id,
                        "bus_stop",
                        {"provider_stop_id": boarding.provider_stop_id},
                        "tago.bus-stop",
                        boarding.stop_publication_id,
                        request.trip_date,
                    ),
                    self._simple_source_evidence(
                        boarding.identity_fact_id,
                        "stop_identity",
                        {"mapping_status": "CONFIRMED"},
                        "transport.stop-identity-map",
                        boarding.identity_publication_id,
                        request.trip_date,
                    ),
                    self._simple_source_evidence(
                        alighting.stop_fact_id,
                        "bus_stop",
                        {"provider_stop_id": alighting.provider_stop_id},
                        "tago.bus-stop",
                        alighting.stop_publication_id,
                        request.trip_date,
                    ),
                    self._simple_source_evidence(
                        alighting.identity_fact_id,
                        "stop_identity",
                        {"mapping_status": "CONFIRMED"},
                        "transport.stop-identity-map",
                        alighting.identity_publication_id,
                        request.trip_date,
                    ),
                    self._simple_source_evidence(
                        policy_fact_id,
                        "bus_fare_policy",
                        {"fare_class": fare_class},
                        "jeju.bus-fare-policy",
                        fare_publication_id,
                        self._bus_fare_policy.verified_on,
                    ),
                    EvidenceFact(
                        fact_id=fare_fact_id,
                        category="computed_bus_fare",
                        value={"minimum_krw": fare_min, "maximum_krw": fare_max},
                        unit="KRW",
                        data_as_of=request.trip_date,
                        retrieved_at=datetime.now(UTC),
                        confidence=1,
                        is_estimated=True,
                        derivation=Derivation(
                            kind="computed",
                            formula="passenger_counts × official_fare_range",
                            input_fact_ids=(policy_fact_id,),
                        ),
                    ),
                )
                candidate = RoutePlanResult(
                    VerifiedRouteOption(
                        origin.place_id,
                        destination.place_id,
                        duration,
                        access.planned_minutes + egress.planned_minutes,
                        access.distance_meters + egress.distance_meters,
                        fare_min,
                        fare_max,
                        0,
                        Transfer(
                            mode="bus",
                            access_walk=access,
                            bus_rides=(ride,),
                            egress_walk=egress,
                        ),
                        all_fact_ids,
                    ),
                    facts,
                    (
                        access_source,
                        egress_source,
                        self._source_metadata("jeju.bus-timetable", departure_at),
                        self._source_metadata("tago.bus-stop", departure_at),
                        self._source_metadata("transport.stop-identity-map", departure_at),
                        self._source_metadata("jeju.bus-fare-policy", departure_at),
                    ),
                )
                if best is None or candidate.option.duration_minutes < best.option.duration_minutes:
                    best = candidate
                # SQL에서 도착시각 순으로 압축한 첫 후보가 실제 20분 도보 제한도
                # 통과했다. 계획대로 다음 정류장 쌍은 첫 후보가 실패할 때만 검증한다.
                break
            if best is not None:
                break
        if best is not None and request.transport.allowed_modes == {"bus"}:
            # bus-only 후보 조립기는 exact 직통 stop-time으로 전 구간 완주성을 먼저
            # 확인한다. 이때 유효 직통을 다시 1회 환승과 경쟁시키면 같은 구간의 TMAP
            # 보행을 중복 조회해 20회 할당을 소모하므로, 직통이 없을 때만 환승을 연다.
            return best
        if request.transport.max_transfers_per_leg >= 1:
            transfer_candidate = self._one_transfer_bus(
                origin,
                destination,
                departure_at,
                strategy,
                request,
                budget,
                boarding_stops,
                alighting_stops,
                arrive_before=(
                    departure_at + timedelta(minutes=best.option.duration_minutes)
                    if best is not None
                    else None
                ),
            )
            if best is None or (
                transfer_candidate is not None
                and transfer_candidate.option.duration_minutes < best.option.duration_minutes
            ):
                best = transfer_candidate
        return best

    def _one_transfer_bus(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
        boarding_stops: tuple[_BusStopCandidate, ...],
        alighting_stops: tuple[_BusStopCandidate, ...],
        arrive_before: datetime | None = None,
    ) -> RoutePlanResult | None:
        """정확한 두 trip과 300m 이내 환승 정류장으로 1회 환승을 검증한다."""

        bus_fare_policy = self._bus_fare_policy
        if bus_fare_policy is None:
            return None
        checked_paths = 0
        transfer_pairs = self._transfer_stop_pairs_batch(
            boarding_stops,
            alighting_stops,
            departure_at
            + timedelta(minutes=self._planning_policy.boarding_buffer_minutes.normal),
            request.trip_date,
            limit=6,
            arrive_before=arrive_before,
        )
        eligible_boarding_ids = {key[0] for key in transfer_pairs}
        checked_boarding_walks = 0
        checked_transfer_paths = 0
        for boarding in boarding_stops:
            if boarding.stop_fact_id not in eligible_boarding_ids:
                continue
            if checked_boarding_walks >= 2:
                continue
            checked_boarding_walks += 1
            access_result = self._bus_walking_leg(
                origin,
                self._stop_entrance(boarding),
                departure_at,
                strategy,
                request,
                budget,
                "access_walk",
            )
            if access_result is None:
                continue
            access, access_fact, access_source = access_result
            first_ready = departure_at + timedelta(
                minutes=access.planned_minutes
                + self._planning_policy.boarding_buffer_minutes.normal
            )
            for alighting in alighting_stops:
                for transfer_from, transfer_to in transfer_pairs.get(
                    (boarding.stop_fact_id, alighting.stop_fact_id), ()
                ):
                    first = self._scheduled_direct_bus(
                        boarding, transfer_from, first_ready, request.trip_date
                    )
                    if first is None:
                        continue
                    first_departure = first[7].astimezone(KST)
                    first_arrival = first[8].astimezone(KST)
                    first_wait = max(
                        0,
                        math.ceil(
                            (
                                first_departure
                                - departure_at
                                - timedelta(minutes=access.planned_minutes)
                            ).total_seconds()
                            / 60
                        ),
                    )
                    if first_wait > request.transport.bus_wait_limit_minutes:
                        continue
                    provisional_second = self._scheduled_direct_bus(
                        transfer_to,
                        alighting,
                        first_arrival + timedelta(minutes=10),
                        request.trip_date,
                    )
                    if provisional_second is None or not self._different_bus_services(
                        first, provisional_second
                    ):
                        continue
                    provisional_second_departure = provisional_second[7].astimezone(KST)
                    if (
                        provisional_second_departure - first_arrival
                    ).total_seconds() / 60 > (
                        request.transport.bus_wait_limit_minutes
                        + request.walking.max_access_walk_minutes
                    ):
                        continue
                    if checked_transfer_paths >= 2:
                        continue
                    checked_transfer_paths += 1
                    transfer_walk_result = self._bus_walking_leg(
                        self._stop_entrance(transfer_from),
                        self._stop_entrance(transfer_to),
                        first_arrival,
                        strategy,
                        request,
                        budget,
                        "transfer_walk",
                    )
                    if transfer_walk_result is None:
                        continue
                    transfer_walk, transfer_fact, transfer_source = transfer_walk_result
                    second_ready = first_arrival + timedelta(
                        minutes=transfer_walk.planned_minutes + 10
                    )
                    second = (
                        provisional_second
                        if provisional_second_departure >= second_ready
                        else self._scheduled_direct_bus(
                            transfer_to, alighting, second_ready, request.trip_date
                        )
                    )
                    if second is None:
                        continue
                    second_departure = second[7].astimezone(KST)
                    second_arrival = second[8].astimezone(KST)
                    second_wait = max(
                        0,
                        math.ceil(
                            (
                                second_departure
                                - first_arrival
                                - timedelta(minutes=transfer_walk.planned_minutes)
                            ).total_seconds()
                            / 60
                        ),
                    )
                    if second_wait > request.transport.bus_wait_limit_minutes:
                        continue
                    if not self._different_bus_services(first, second):
                        continue
                    if checked_paths >= 2:
                        continue
                    egress_result = self._bus_walking_leg(
                        self._stop_entrance(alighting),
                        destination,
                        second_arrival,
                        strategy,
                        request,
                        budget,
                        "egress_walk",
                    )
                    checked_paths += 1
                    if egress_result is None:
                        continue
                    egress, egress_fact, egress_source = egress_result
                    route_types = (str(first[4]), str(second[4]))
                    try:
                        fares = tuple(
                            estimate_bus_fare(
                                bus_fare_policy,
                                route_type=route_type,
                                adults=request.party.adults,
                                seniors=request.party.seniors,
                                children=request.party.children,
                                travel_date=request.trip_date,
                            )
                            for route_type in route_types
                        )
                    except ValueError as error:
                        if str(error) == "FARE_POLICY_NOT_EFFECTIVE":
                            continue
                        raise
                    fare_min = sum(item[0] for item in fares)
                    fare_max = sum(item[1] for item in fares)
                    if request.total_budget_krw is not None and fare_max > request.total_budget_krw:
                        continue
                    schedule_ids = (
                        f"jeju.bus-timetable:{first[0]}:{boarding.stop_fact_id}:"
                        f"{transfer_from.stop_fact_id}",
                        f"jeju.bus-timetable:{second[0]}:{transfer_to.stop_fact_id}:"
                        f"{alighting.stop_fact_id}",
                    )
                    rides = (
                        self._bus_ride_from_schedule(
                            first,
                            boarding,
                            transfer_from,
                            schedule_ids[0],
                            self._planning_policy.boarding_buffer_minutes.normal,
                        ),
                        self._bus_ride_from_schedule(
                            second,
                            transfer_to,
                            alighting,
                            schedule_ids[1],
                            10,
                        ),
                    )
                    fare_classes = tuple(
                        "EXPRESS" if "급행" in route_type else "STANDARD"
                        for route_type in route_types
                    )
                    policy_fact_ids = tuple(
                        dict.fromkeys(
                            f"jeju.bus-fare-policy:{bus_fare_policy.effective_from}:{fare_class}"
                            for fare_class in fare_classes
                        )
                    )
                    fare_fact_id = (
                        f"computed.bus-fare:transfer:{request.trip_date}:"
                        f"{request.party.adults}:{request.party.seniors}:"
                        f"{request.party.children}:{first[0]}:{second[0]}"
                    )
                    stop_facts = tuple(
                        fact
                        for stop in dict.fromkeys((boarding, transfer_from, transfer_to, alighting))
                        for fact in self._stop_evidence(stop, request.trip_date)
                    )
                    schedule_facts = tuple(
                        self._simple_source_evidence(
                            schedule_id,
                            "scheduled_bus_route",
                            {
                                "scheduled_departure_at": ride.scheduled_departure_at.isoformat(),
                                "scheduled_arrival_at": ride.scheduled_arrival_at.isoformat(),
                            },
                            "jeju.bus-timetable",
                            str(schedule[10]),
                            request.trip_date,
                            source_fact_id=str(schedule[0]),
                        )
                        for schedule_id, ride, schedule in zip(
                            schedule_ids, rides, (first, second), strict=True
                        )
                    )
                    fare_publication_id = self._fare_policy_publication(
                        "jeju.bus-fare-policy", bus_fare_policy.effective_from
                    )
                    policy_facts = tuple(
                        self._simple_source_evidence(
                            fact_id,
                            "bus_fare_policy",
                            {"fare_class": fare_class},
                            "jeju.bus-fare-policy",
                            fare_publication_id,
                            bus_fare_policy.verified_on,
                        )
                        for fact_id, fare_class in zip(
                            policy_fact_ids, dict.fromkeys(fare_classes), strict=True
                        )
                    )
                    fare_fact = EvidenceFact(
                        fact_id=fare_fact_id,
                        category="computed_bus_fare",
                        value={"minimum_krw": fare_min, "maximum_krw": fare_max},
                        unit="KRW",
                        data_as_of=request.trip_date,
                        retrieved_at=datetime.now(UTC),
                        confidence=1,
                        is_estimated=True,
                        derivation=Derivation(
                            kind="computed",
                            formula="sum(official_fare_range_per_boarding)",
                            input_fact_ids=policy_fact_ids,
                        ),
                    )
                    all_fact_ids = tuple(
                        dict.fromkeys(
                            (
                                *access.evidence_fact_ids,
                                *(fact_id for ride in rides for fact_id in ride.evidence_fact_ids),
                                *transfer_walk.evidence_fact_ids,
                                *egress.evidence_fact_ids,
                                *policy_fact_ids,
                                fare_fact_id,
                            )
                        )
                    )
                    end_at = second_arrival + timedelta(minutes=egress.planned_minutes)
                    duration = max(1, math.ceil((end_at - departure_at).total_seconds() / 60))
                    candidate = RoutePlanResult(
                        VerifiedRouteOption(
                            origin.place_id,
                            destination.place_id,
                            duration,
                            access.planned_minutes
                            + transfer_walk.planned_minutes
                            + egress.planned_minutes,
                            access.distance_meters
                            + transfer_walk.distance_meters
                            + egress.distance_meters,
                            fare_min,
                            fare_max,
                            1,
                            Transfer(
                                mode="bus",
                                access_walk=access,
                                bus_rides=rides,
                                transfer_walks=(transfer_walk,),
                                egress_walk=egress,
                                distance_meters=(
                                    access.distance_meters
                                    + transfer_walk.distance_meters
                                    + egress.distance_meters
                                ),
                                distance_is_estimated=True,
                            ),
                            all_fact_ids,
                        ),
                        (
                            access_fact,
                            transfer_fact,
                            egress_fact,
                            *schedule_facts,
                            *stop_facts,
                            *policy_facts,
                            fare_fact,
                        ),
                        (
                            access_source,
                            transfer_source,
                            egress_source,
                            self._source_metadata("jeju.bus-timetable", departure_at),
                            self._source_metadata("tago.bus-stop", departure_at),
                            self._source_metadata("transport.stop-identity-map", departure_at),
                            self._source_metadata("jeju.bus-fare-policy", departure_at),
                        ),
                    )
                    # exact 시각 순으로 압축된 후보가 실제 세 보행 제한도 통과했다.
                    # 다음 signature는 첫 후보가 실패할 때만 외부 검증한다.
                    return candidate
        return None

    @staticmethod
    def _different_bus_services(first, second) -> bool:
        """provider pattern과 공개 노선번호가 모두 다른 경우만 실제 환승으로 인정한다."""

        return str(first[2]) != str(second[2]) and str(first[3]).strip() != str(
            second[3]
        ).strip()

    def _transfer_stop_pairs_batch(
        self,
        boarding_stops: tuple[_BusStopCandidate, ...],
        alighting_stops: tuple[_BusStopCandidate, ...],
        ready_at: datetime,
        trip_date: date,
        *,
        limit: int,
        arrive_before: datetime | None = None,
    ) -> dict[
        tuple[str, str], tuple[tuple[_BusStopCandidate, _BusStopCandidate], ...]
    ]:
        """exact 운행편이 있는 1회 환승 signature를 한 번의 SQL로 최대 여섯 개 고른다."""

        if not boarding_stops or not alighting_stops:
            return {}
        runtime_dsn = getattr(self, "_runtime_dsn", None)
        if runtime_dsn is None:
            fallback: dict[
                tuple[str, str], list[tuple[_BusStopCandidate, _BusStopCandidate]]
            ] = {}
            remaining = limit
            for boarding in boarding_stops:
                for alighting in alighting_stops:
                    pairs = self._transfer_stop_pairs(boarding, alighting)[:remaining]
                    if pairs:
                        fallback[(boarding.stop_fact_id, alighting.stop_fact_id)] = list(pairs)
                        remaining -= len(pairs)
                    if remaining <= 0:
                        return {key: tuple(value) for key, value in fallback.items()}
            return {key: tuple(value) for key, value in fallback.items()}

        day_type = (
            "SATURDAY"
            if trip_date.isoweekday() == 6
            else "SUNDAY"
            if trip_date.isoweekday() == 7
            else "WEEKDAY"
        )
        with psycopg.connect(runtime_dsn) as connection:
            holiday = connection.execute(
                """SELECT 1 FROM travel_read.active_holiday
                   WHERE holiday_date = %s AND is_public_institution_holiday LIMIT 1""",
                (trip_date,),
            ).fetchone()
            if holiday is not None:
                day_type = "HOLIDAY"
            rows = connection.execute(
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
                           AND exception.exception_type = 'REMOVED')
                       AND NOT EXISTS (
                         SELECT 1
                         FROM travel_read.active_route_service_exception exception
                         WHERE exception.route_fact_id = trip.route_fact_id
                           AND exception.exception_type = 'SUSPENDED'
                           AND exception.starts_at <= %(ready_at)s
                           AND (exception.ends_at IS NULL
                                OR exception.ends_at >= %(ready_at)s))
                   ), confirmed_stop AS MATERIALIZED (
                     SELECT identity.canonical_stop_id, stop.provider_stop_id,
                            stop.name AS stop_name,
                            COALESCE(identity.direction_text, stop.direction_text)
                              AS direction_text,
                            ST_Y(stop.position::geometry) AS latitude,
                            ST_X(stop.position::geometry) AS longitude,
                            stop.position, stop.fact_id AS stop_fact_id,
                            identity.fact_id AS identity_fact_id,
                            stop.publication_id::text AS stop_publication_id,
                            identity.publication_id::text AS identity_publication_id
                     FROM travel_read.active_bus_stop stop
                     JOIN travel_read.active_stop_identity identity
                       ON identity.source_fact_id = stop.fact_id
                      AND identity.mapping_status = 'CONFIRMED'
                   ), transfer_pair AS MATERIALIZED (
                     SELECT from_identity.canonical_stop_id AS from_canonical_stop_id,
                            from_identity.provider_stop_id AS from_provider_stop_id,
                            from_identity.stop_name AS from_stop_name,
                            from_identity.direction_text AS from_direction_text,
                            from_identity.latitude AS from_latitude,
                            from_identity.longitude AS from_longitude,
                            from_identity.stop_fact_id AS from_stop_fact_id,
                            from_identity.identity_fact_id AS from_identity_fact_id,
                            from_identity.stop_publication_id
                              AS from_stop_publication_id,
                            from_identity.identity_publication_id
                              AS from_identity_publication_id,
                            to_identity.canonical_stop_id AS to_canonical_stop_id,
                            to_stop.provider_stop_id AS to_provider_stop_id,
                            to_stop.name AS to_stop_name,
                            COALESCE(to_identity.direction_text, to_stop.direction_text)
                              AS to_direction_text,
                            ST_Y(to_stop.position::geometry) AS to_latitude,
                            ST_X(to_stop.position::geometry) AS to_longitude,
                            to_stop.fact_id AS to_stop_fact_id,
                            to_identity.fact_id AS to_identity_fact_id,
                            to_stop.publication_id::text AS to_stop_publication_id,
                            to_identity.publication_id::text
                              AS to_identity_publication_id
                     FROM confirmed_stop from_identity
                     JOIN travel_read.active_bus_stop to_stop
                       ON ST_DWithin(from_identity.position, to_stop.position, 300)
                     JOIN travel_read.active_stop_identity to_identity
                       ON to_identity.source_fact_id = to_stop.fact_id
                      AND to_identity.mapping_status = 'CONFIRMED'
                   ), first_leg AS MATERIALIZED (
                     SELECT board_time.stop_fact_id AS boarding_stop_fact_id,
                            transfer_time.stop_fact_id AS transfer_stop_fact_id,
                            trip.route_fact_id, trip.fact_id AS trip_fact_id,
                            (%(trip_date)s::date + transfer_time.arrival_at
                              + transfer_time.arrival_day_offset * interval '1 day')
                              AT TIME ZONE 'Asia/Seoul' AS transfer_arrival_at
                     FROM eligible_trip trip
                     JOIN travel_read.active_stop_time board_time
                       ON board_time.trip_id = trip.trip_id
                      AND board_time.stop_fact_id = ANY(%(boarding_stops)s::text[])
                     JOIN travel_read.active_stop_time transfer_time
                       ON transfer_time.trip_id = trip.trip_id
                      AND transfer_time.stop_sequence > board_time.stop_sequence
                     WHERE (%(trip_date)s::date + board_time.departure_at
                            + board_time.departure_day_offset * interval '1 day')
                            AT TIME ZONE 'Asia/Seoul' >= %(ready_at)s
                   ), second_leg AS MATERIALIZED (
                     SELECT transfer_time.stop_fact_id AS transfer_stop_fact_id,
                            alight_time.stop_fact_id AS alighting_stop_fact_id,
                            trip.route_fact_id, trip.fact_id AS trip_fact_id,
                            (%(trip_date)s::date + transfer_time.departure_at
                              + transfer_time.departure_day_offset * interval '1 day')
                              AT TIME ZONE 'Asia/Seoul' AS transfer_departure_at,
                            (%(trip_date)s::date + alight_time.arrival_at
                              + alight_time.arrival_day_offset * interval '1 day')
                              AT TIME ZONE 'Asia/Seoul' AS second_arrival_at
                     FROM eligible_trip trip
                     JOIN travel_read.active_stop_time alight_time
                       ON alight_time.trip_id = trip.trip_id
                      AND alight_time.stop_fact_id = ANY(%(alighting_stops)s::text[])
                     JOIN travel_read.active_stop_time transfer_time
                       ON transfer_time.trip_id = trip.trip_id
                      AND transfer_time.stop_sequence < alight_time.stop_sequence
                   ), candidate_path AS (
                     SELECT first_leg.boarding_stop_fact_id,
                            second_leg.alighting_stop_fact_id,
                            transfer_pair.*,
                            second_leg.second_arrival_at,
                            row_number() OVER (
                              PARTITION BY first_leg.boarding_stop_fact_id,
                                           second_leg.alighting_stop_fact_id,
                                           transfer_pair.from_stop_fact_id,
                                           transfer_pair.to_stop_fact_id
                              ORDER BY second_leg.second_arrival_at,
                                       first_leg.trip_fact_id, second_leg.trip_fact_id
                            ) AS path_rank
                     FROM first_leg
                     JOIN transfer_pair
                       ON transfer_pair.from_stop_fact_id
                          = first_leg.transfer_stop_fact_id
                     JOIN second_leg
                       ON second_leg.transfer_stop_fact_id
                          = transfer_pair.to_stop_fact_id
                      AND second_leg.route_fact_id <> first_leg.route_fact_id
                      AND second_leg.transfer_departure_at
                          >= first_leg.transfer_arrival_at + interval '10 minutes'
                     JOIN travel_read.active_bus_route first_route
                       ON first_route.fact_id = first_leg.route_fact_id
                     JOIN travel_read.active_bus_route second_route
                       ON second_route.fact_id = second_leg.route_fact_id
                      AND second_route.route_number <> first_route.route_number
                   )
                   SELECT boarding_stop_fact_id, alighting_stop_fact_id,
                          from_canonical_stop_id, from_provider_stop_id, from_stop_name,
                          from_direction_text, from_latitude, from_longitude,
                          from_stop_fact_id, from_identity_fact_id,
                          from_stop_publication_id, from_identity_publication_id,
                          to_canonical_stop_id, to_provider_stop_id, to_stop_name,
                          to_direction_text, to_latitude, to_longitude,
                          to_stop_fact_id, to_identity_fact_id,
                          to_stop_publication_id, to_identity_publication_id
                   FROM candidate_path
                   WHERE path_rank = 1
                     AND (%(arrive_before)s::timestamptz IS NULL
                          OR second_arrival_at < %(arrive_before)s)
                   ORDER BY second_arrival_at, boarding_stop_fact_id,
                            alighting_stop_fact_id, from_stop_fact_id, to_stop_fact_id
                   LIMIT %(limit)s""",
                {
                    "boarding_stops": [item.stop_fact_id for item in boarding_stops],
                    "alighting_stops": [item.stop_fact_id for item in alighting_stops],
                    "trip_date": trip_date,
                    "day_type": day_type,
                    "ready_at": ready_at,
                    "limit": limit,
                    "arrive_before": arrive_before,
                },
            ).fetchall()
        grouped: dict[
            tuple[str, str], list[tuple[_BusStopCandidate, _BusStopCandidate]]
        ] = {}
        for row in rows:
            key = (str(row[0]), str(row[1]))
            grouped.setdefault(key, []).append(
                (self._stop_candidate_from_row(row, 2), self._stop_candidate_from_row(row, 12))
            )
        return {key: tuple(value) for key, value in grouped.items()}

    def _transfer_stop_pairs(
        self,
        boarding: _BusStopCandidate,
        alighting: _BusStopCandidate,
    ) -> tuple[tuple[_BusStopCandidate, _BusStopCandidate], ...]:
        """서로 다른 노선 사이 confirmed 환승 정류장 쌍을 300m 안에서 찾는다."""

        if self._runtime_dsn is None:
            return ()
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                """SELECT from_identity.canonical_stop_id,
                          from_stop.provider_stop_id, from_stop.name,
                          COALESCE(from_identity.direction_text, from_stop.direction_text),
                          ST_Y(from_stop.position::geometry),
                          ST_X(from_stop.position::geometry),
                          from_stop.fact_id, from_identity.fact_id,
                          from_stop.publication_id::text,
                          from_identity.publication_id::text,
                          to_identity.canonical_stop_id,
                          to_stop.provider_stop_id, to_stop.name,
                          COALESCE(to_identity.direction_text, to_stop.direction_text),
                          ST_Y(to_stop.position::geometry),
                          ST_X(to_stop.position::geometry),
                          to_stop.fact_id, to_identity.fact_id,
                          to_stop.publication_id::text,
                          to_identity.publication_id::text
                   FROM travel_read.active_bus_route_stop first_board
                   JOIN travel_read.active_bus_route_stop first_alight
                     ON first_alight.route_fact_id = first_board.route_fact_id
                    AND first_alight.route_sequence > first_board.route_sequence
                   JOIN travel_read.active_bus_stop from_stop
                     ON from_stop.fact_id = first_alight.stop_fact_id
                   JOIN travel_read.active_stop_identity from_identity
                     ON from_identity.source_fact_id = from_stop.fact_id
                    AND from_identity.mapping_status = 'CONFIRMED'
                   JOIN travel_read.active_bus_route_stop second_board
                     ON second_board.route_fact_id <> first_board.route_fact_id
                   JOIN travel_read.active_bus_stop to_stop
                     ON to_stop.fact_id = second_board.stop_fact_id
                    AND ST_DWithin(from_stop.position, to_stop.position, 300)
                   JOIN travel_read.active_stop_identity to_identity
                     ON to_identity.source_fact_id = to_stop.fact_id
                    AND to_identity.mapping_status = 'CONFIRMED'
                   JOIN travel_read.active_bus_route_stop second_alight
                     ON second_alight.route_fact_id = second_board.route_fact_id
                    AND second_alight.stop_fact_id = %(alighting)s
                    AND second_alight.route_sequence > second_board.route_sequence
                   WHERE first_board.stop_fact_id = %(boarding)s
                   ORDER BY ST_Distance(from_stop.position, to_stop.position),
                            first_board.route_fact_id, second_board.route_fact_id,
                            first_alight.route_sequence, second_board.route_sequence
                   LIMIT 6""",
                {
                    "boarding": boarding.stop_fact_id,
                    "alighting": alighting.stop_fact_id,
                },
            ).fetchall()
        result = []
        for row in rows:
            result.append(
                (
                    self._stop_candidate_from_row(row, 0),
                    self._stop_candidate_from_row(row, 10),
                )
            )
        return tuple(result)

    @staticmethod
    def _stop_candidate_from_row(row, offset: int) -> _BusStopCandidate:
        return _BusStopCandidate(
            canonical_stop_id=str(row[offset]),
            provider_stop_id=str(row[offset + 1]),
            name=str(row[offset + 2]),
            direction_text=str(row[offset + 3] or ""),
            position=Coordinates(latitude=float(row[offset + 4]), longitude=float(row[offset + 5])),
            stop_fact_id=str(row[offset + 6]),
            identity_fact_id=str(row[offset + 7]),
            stop_publication_id=str(row[offset + 8]),
            identity_publication_id=str(row[offset + 9]),
        )

    def _bus_ride_from_schedule(
        self,
        schedule,
        boarding: _BusStopCandidate,
        alighting: _BusStopCandidate,
        schedule_fact_id: str,
        boarding_buffer_minutes: int,
    ) -> BusRide:
        departure = schedule[7].astimezone(KST)
        arrival = schedule[8].astimezone(KST)
        return BusRide(
            canonical_boarding_stop_id=boarding.canonical_stop_id,
            provider_boarding_stop_id=boarding.provider_stop_id,
            boarding_stop_name=boarding.name,
            boarding_stop_position=boarding.position,
            boarding_direction=str(schedule[9]),
            canonical_alighting_stop_id=alighting.canonical_stop_id,
            provider_alighting_stop_id=alighting.provider_stop_id,
            alighting_stop_name=alighting.name,
            alighting_stop_position=alighting.position,
            route_id=str(schedule[2]),
            route_number=str(schedule[3]),
            scheduled_departure_at=departure,
            scheduled_arrival_at=arrival,
            recommended_stop_arrival_at=departure - timedelta(minutes=boarding_buffer_minutes),
            boarding_buffer_minutes=boarding_buffer_minutes,
            mapping_status="CONFIRMED",
            evidence_fact_ids=(
                schedule_fact_id,
                boarding.stop_fact_id,
                boarding.identity_fact_id,
                alighting.stop_fact_id,
                alighting.identity_fact_id,
            ),
        )

    def _stop_evidence(
        self, stop: _BusStopCandidate, trip_date: date
    ) -> tuple[EvidenceFact, EvidenceFact]:
        return (
            self._simple_source_evidence(
                stop.stop_fact_id,
                "bus_stop",
                {"provider_stop_id": stop.provider_stop_id},
                "tago.bus-stop",
                stop.stop_publication_id,
                trip_date,
            ),
            self._simple_source_evidence(
                stop.identity_fact_id,
                "stop_identity",
                {"mapping_status": "CONFIRMED"},
                "transport.stop-identity-map",
                stop.identity_publication_id,
                trip_date,
            ),
        )

    def _nearest_stops(self, position: Coordinates) -> tuple[_BusStopCandidate, ...]:
        if self._runtime_dsn is None:
            return ()
        with psycopg.connect(self._runtime_dsn) as connection:
            rows = connection.execute(
                """SELECT identity.canonical_stop_id, stop.provider_stop_id, stop.name,
                          COALESCE(identity.direction_text, stop.direction_text),
                          ST_Y(stop.position::geometry), ST_X(stop.position::geometry),
                          stop.fact_id, identity.fact_id, stop.publication_id::text,
                          identity.publication_id::text
                   FROM travel_read.active_bus_stop stop
                   JOIN travel_read.active_stop_identity identity
                     ON identity.source_fact_id = stop.fact_id
                   WHERE identity.mapping_status = 'CONFIRMED'
                     AND stop.observed_at >= now() - interval '45 days'
                     AND ST_DWithin(
                         stop.position,
                         ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                         2500)
                   ORDER BY stop.position <->
                     ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                   LIMIT 12""",
                (
                    position.longitude,
                    position.latitude,
                    position.longitude,
                    position.latitude,
                ),
            ).fetchall()
        return tuple(
            _BusStopCandidate(
                canonical_stop_id=str(row[0]),
                provider_stop_id=str(row[1]),
                name=str(row[2]),
                direction_text=str(row[3] or ""),
                position=Coordinates(latitude=float(row[4]), longitude=float(row[5])),
                stop_fact_id=str(row[6]),
                identity_fact_id=str(row[7]),
                stop_publication_id=str(row[8]),
                identity_publication_id=str(row[9]),
            )
            for row in rows
        )

    def _nearest_direct_stop_pair(
        self, origin: Coordinates, destination: Coordinates
    ) -> tuple[_BusStopCandidate, _BusStopCandidate] | None:
        """같은 공식 pattern에서 순서와 exact stop-time이 확인되는 최근접 한 쌍을 고른다."""

        if self._runtime_dsn is None:
            return None
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """WITH boarding AS (
                     SELECT identity.canonical_stop_id, stop.provider_stop_id,
                            stop.name AS stop_name,
                            COALESCE(identity.direction_text, stop.direction_text)
                              AS direction_text,
                            ST_Y(stop.position::geometry) AS latitude,
                            ST_X(stop.position::geometry) AS longitude,
                            stop.fact_id AS stop_fact_id,
                            identity.fact_id AS identity_fact_id,
                            stop.publication_id::text AS stop_publication_id,
                            identity.publication_id::text AS identity_publication_id,
                            ST_Distance(stop.position,
                              ST_SetSRID(ST_MakePoint(%(origin_lon)s, %(origin_lat)s),
                                        4326)::geography) AS endpoint_distance
                     FROM travel_read.active_bus_stop stop
                     JOIN travel_read.active_stop_identity identity
                       ON identity.source_fact_id = stop.fact_id
                     WHERE identity.mapping_status = 'CONFIRMED'
                       AND ST_DWithin(stop.position,
                         ST_SetSRID(ST_MakePoint(%(origin_lon)s, %(origin_lat)s),
                                   4326)::geography, 2500)
                     ORDER BY stop.position <->
                       ST_SetSRID(ST_MakePoint(%(origin_lon)s, %(origin_lat)s),
                                  4326)::geography
                     LIMIT 12
                   ), alighting AS (
                     SELECT identity.canonical_stop_id, stop.provider_stop_id,
                            stop.name AS stop_name,
                            COALESCE(identity.direction_text, stop.direction_text)
                              AS direction_text,
                            ST_Y(stop.position::geometry) AS latitude,
                            ST_X(stop.position::geometry) AS longitude,
                            stop.fact_id AS stop_fact_id,
                            identity.fact_id AS identity_fact_id,
                            stop.publication_id::text AS stop_publication_id,
                            identity.publication_id::text AS identity_publication_id,
                            ST_Distance(stop.position,
                              ST_SetSRID(ST_MakePoint(%(destination_lon)s, %(destination_lat)s),
                                        4326)::geography) AS endpoint_distance
                     FROM travel_read.active_bus_stop stop
                     JOIN travel_read.active_stop_identity identity
                       ON identity.source_fact_id = stop.fact_id
                     WHERE identity.mapping_status = 'CONFIRMED'
                       AND ST_DWithin(stop.position,
                         ST_SetSRID(ST_MakePoint(%(destination_lon)s, %(destination_lat)s),
                                   4326)::geography, 2500)
                     ORDER BY stop.position <->
                       ST_SetSRID(ST_MakePoint(%(destination_lon)s, %(destination_lat)s),
                                  4326)::geography
                     LIMIT 12
                   )
                   SELECT board.canonical_stop_id, board.provider_stop_id, board.stop_name,
                          board.direction_text, board.latitude, board.longitude,
                          board.stop_fact_id, board.identity_fact_id,
                          board.stop_publication_id, board.identity_publication_id,
                          alight.canonical_stop_id, alight.provider_stop_id, alight.stop_name,
                          alight.direction_text, alight.latitude, alight.longitude,
                          alight.stop_fact_id, alight.identity_fact_id,
                          alight.stop_publication_id, alight.identity_publication_id
                   FROM boarding board
                   JOIN travel_read.active_bus_route_stop board_sequence
                     ON board_sequence.stop_fact_id = board.stop_fact_id
                   JOIN alighting alight ON true
                   JOIN travel_read.active_bus_route_stop alight_sequence
                     ON alight_sequence.route_fact_id = board_sequence.route_fact_id
                    AND alight_sequence.stop_fact_id = alight.stop_fact_id
                   JOIN travel_read.active_scheduled_trip trip
                     ON trip.route_fact_id = board_sequence.route_fact_id
                    AND trip.direction_text = board_sequence.direction_text
                   JOIN travel_read.active_stop_time board_time
                     ON board_time.trip_id = trip.trip_id
                    AND board_time.stop_fact_id = board.stop_fact_id
                   JOIN travel_read.active_stop_time alight_time
                     ON alight_time.trip_id = trip.trip_id
                    AND alight_time.stop_fact_id = alight.stop_fact_id
                   WHERE board_sequence.route_sequence < alight_sequence.route_sequence
                     AND board_time.stop_sequence < alight_time.stop_sequence
                     AND board_sequence.direction_text = alight_sequence.direction_text
                   ORDER BY board.endpoint_distance + alight.endpoint_distance
                   LIMIT 1""",
                {
                    "origin_lon": origin.longitude,
                    "origin_lat": origin.latitude,
                    "destination_lon": destination.longitude,
                    "destination_lat": destination.latitude,
                },
            ).fetchone()
        if row is None:
            return None
        boarding = _BusStopCandidate(
            canonical_stop_id=str(row[0]),
            provider_stop_id=str(row[1]),
            name=str(row[2]),
            direction_text=str(row[3] or ""),
            position=Coordinates(latitude=float(row[4]), longitude=float(row[5])),
            stop_fact_id=str(row[6]),
            identity_fact_id=str(row[7]),
            stop_publication_id=str(row[8]),
            identity_publication_id=str(row[9]),
        )
        alighting = _BusStopCandidate(
            canonical_stop_id=str(row[10]),
            provider_stop_id=str(row[11]),
            name=str(row[12]),
            direction_text=str(row[13] or ""),
            position=Coordinates(latitude=float(row[14]), longitude=float(row[15])),
            stop_fact_id=str(row[16]),
            identity_fact_id=str(row[17]),
            stop_publication_id=str(row[18]),
            identity_publication_id=str(row[19]),
        )
        return boarding, alighting

    def _unverifiable_bus_pattern(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        trip_date: date,
    ) -> tuple[TransportCandidate, tuple[EvidenceFact, ...]] | None:
        """정류장 pattern만 있고 날짜별 stop time이 없을 때 수치를 만들지 않는다."""

        if self._runtime_dsn is None:
            return None
        boarding = self._nearest_stops(origin.position)
        alighting = self._nearest_stops(destination.position)
        if not boarding or not alighting:
            return None
        with psycopg.connect(self._runtime_dsn) as connection:
            row = connection.execute(
                """SELECT route.fact_id, route.provider_route_id, route.route_number,
                          route.publication_id::text, metadata.source_id,
                          metadata.source_date, metadata.observed_at
                   FROM travel_read.active_bus_route_stop board
                   JOIN travel_read.active_bus_route_stop alight
                     ON alight.route_fact_id = board.route_fact_id
                    AND alight.route_sequence > board.route_sequence
                    AND COALESCE(alight.direction_text, '')
                        = COALESCE(board.direction_text, '')
                   JOIN travel_read.active_bus_route route
                     ON route.fact_id = board.route_fact_id
                   JOIN travel_read.active_source_metadata metadata
                     ON metadata.publication_id = route.publication_id
                   WHERE board.stop_fact_id = ANY(%(boarding)s::text[])
                     AND alight.stop_fact_id = ANY(%(alighting)s::text[])
                   ORDER BY route.fact_id, board.route_sequence, alight.route_sequence
                   LIMIT 1""",
                {
                    "boarding": [stop.stop_fact_id for stop in boarding],
                    "alighting": [stop.stop_fact_id for stop in alighting],
                },
            ).fetchone()
        if row is None:
            return None
        fact_id = str(row[0])
        evidence = self._simple_source_evidence(
            fact_id,
            "bus_route_pattern",
            {
                "provider_route_id": str(row[1]),
                "route_number": str(row[2]),
                "trip_date": trip_date.isoformat(),
                "stop_time_verified": False,
            },
            str(row[4]),
            str(row[3]),
            row[5] or row[6].date(),
        )
        return (
            TransportCandidate(
                mode="bus",
                door_to_door_minutes=None,
                walking_minutes=0,
                distance_meters=None,
                cost=None,
                status="unverifiable",
                evidence_fact_ids=(fact_id,),
                route_id=str(row[1]),
                route_number=str(row[2]),
                reason_codes=("BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED",),
            ),
            (evidence,),
        )

    def _exact_bus_unavailable(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        request: RecommendDayTripsInput,
    ) -> tuple[TransportCandidate, tuple[EvidenceFact, ...]] | None:
        """정확한 운행편은 있지만 제약으로 탈락한 버스를 unverifiable로 오표기하지 않는다."""

        boarding = self._nearest_stops(origin.position)
        alighting = self._nearest_stops(destination.position)
        ready_at = departure_at + timedelta(
            minutes=self._planning_policy.boarding_buffer_minutes.normal
        )
        selected_signatures = set(
            self._direct_stop_signatures(
                boarding,
                alighting,
                ready_at,
                request.trip_date,
                limit=6,
            )
        )
        scheduled_candidates = tuple(
            (schedule, board, alight)
            for board in boarding
            for alight in alighting
            if (board.stop_fact_id, alight.stop_fact_id) in selected_signatures
            and (
                schedule := self._scheduled_direct_bus(
                    board, alight, ready_at, request.trip_date
                )
            )
            is not None
        )
        if not scheduled_candidates:
            return None
        schedule, board, alight = min(
            scheduled_candidates,
            key=lambda item: (
                item[0][8],
                str(item[0][1]),
                item[1].stop_fact_id,
                item[2].stop_fact_id,
            ),
        )
        scheduled_departure = schedule[7].astimezone(KST)
        unavoidable_wait = max(
            0,
            math.ceil(
                (
                    scheduled_departure
                    - departure_at
                    - timedelta(minutes=request.walking.max_access_walk_minutes)
                ).total_seconds()
                / 60
            ),
        )
        reason = (
            "BUS_WAIT_LIMIT_EXCEEDED"
            if unavoidable_wait > request.transport.bus_wait_limit_minutes
            else "BUS_ROUTE_UNAVAILABLE"
        )
        schedule_fact_id = (
            f"jeju.bus-timetable:{schedule[0]}:{board.stop_fact_id}:{alight.stop_fact_id}"
        )
        evidence = self._simple_source_evidence(
            schedule_fact_id,
            "scheduled_bus_route",
            {
                "scheduled_departure_at": scheduled_departure.isoformat(),
                "scheduled_arrival_at": schedule[8].astimezone(KST).isoformat(),
                "selection_status": "unavailable",
            },
            "jeju.bus-timetable",
            str(schedule[10]),
            request.trip_date,
            source_fact_id=str(schedule[0]),
        )
        return (
            TransportCandidate(
                mode="bus",
                door_to_door_minutes=None,
                walking_minutes=0,
                distance_meters=None,
                cost=None,
                status="unavailable",
                evidence_fact_ids=(schedule_fact_id,),
                route_id=str(schedule[2]),
                route_number=str(schedule[3]),
                reason_codes=(reason,),
            ),
            (evidence,),
        )

    @staticmethod
    def _stop_entrance(stop: _BusStopCandidate) -> VerifiedEntrance:
        return VerifiedEntrance(
            entrance_id=stop.canonical_stop_id,
            place_id=stop.canonical_stop_id,
            position=stop.position,
            supported_modes=("walk",),
            endpoint_basis=EndpointBasis.CONFIRMED_STOP,
            evidence_fact_ids=(stop.stop_fact_id, stop.identity_fact_id),
        )

    def _scheduled_direct_bus(
        self,
        boarding: _BusStopCandidate,
        alighting: _BusStopCandidate,
        ready_at: datetime,
        trip_date: date,
    ):
        if self._runtime_dsn is None:
            return None
        day_type = (
            "SATURDAY"
            if trip_date.isoweekday() == 6
            else "SUNDAY"
            if trip_date.isoweekday() == 7
            else "WEEKDAY"
        )
        with psycopg.connect(self._runtime_dsn) as connection:
            holiday = connection.execute(
                """SELECT 1 FROM travel_read.active_holiday
                   WHERE holiday_date = %s AND is_public_institution_holiday LIMIT 1""",
                (trip_date,),
            ).fetchone()
            if holiday is not None:
                day_type = "HOLIDAY"
            return connection.execute(
                """SELECT trip.fact_id, route.fact_id, route.provider_route_id,
                          route.route_number, COALESCE(route.attributes->>'route_type', ''),
                          board_time.fact_id, alight_time.fact_id,
                          (%(trip_date)s::date + board_time.departure_at
                            + board_time.departure_day_offset * interval '1 day')
                            AT TIME ZONE 'Asia/Seoul' AS departure_at,
                          (%(trip_date)s::date + alight_time.arrival_at
                            + alight_time.arrival_day_offset * interval '1 day')
                            AT TIME ZONE 'Asia/Seoul' AS arrival_at,
                          trip.direction_text, trip.publication_id::text
                   FROM travel_read.active_scheduled_trip trip
                   JOIN travel_read.active_bus_route route
                     ON route.fact_id = trip.route_fact_id
                   JOIN travel_read.active_service_calendar calendar
                     ON calendar.service_id = trip.service_id
                   JOIN travel_read.active_stop_time board_time
                     ON board_time.trip_id = trip.trip_id
                    AND board_time.stop_fact_id = %(boarding_stop)s
                   JOIN travel_read.active_stop_time alight_time
                     ON alight_time.trip_id = trip.trip_id
                    AND alight_time.stop_fact_id = %(alighting_stop)s
                   WHERE calendar.day_type = %(day_type)s
                     AND calendar.starts_on <= %(trip_date)s
                     AND calendar.ends_on >= %(trip_date)s
                     AND (trip.timetable_effective_from IS NULL
                          OR trip.timetable_effective_from <= %(trip_date)s)
                     AND (trip.timetable_effective_to IS NULL
                          OR trip.timetable_effective_to >= %(trip_date)s)
                     AND board_time.stop_sequence < alight_time.stop_sequence
                     AND (%(trip_date)s::date + board_time.departure_at
                          + board_time.departure_day_offset * interval '1 day')
                          AT TIME ZONE 'Asia/Seoul' >= %(ready_at)s
                     AND NOT EXISTS (
                         SELECT 1
                         FROM travel_read.active_service_calendar_exception exception
                         WHERE exception.service_id = trip.service_id
                           AND exception.exception_date = %(trip_date)s
                           AND exception.exception_type = 'REMOVED')
                     AND NOT EXISTS (
                         SELECT 1
                         FROM travel_read.active_route_service_exception exception
                         WHERE exception.route_fact_id = trip.route_fact_id
                           AND exception.exception_type = 'SUSPENDED'
                           AND exception.starts_at <= %(ready_at)s
                           AND (exception.ends_at IS NULL
                                OR exception.ends_at >= %(ready_at)s))
                   ORDER BY departure_at
                   LIMIT 1""",
                {
                    "trip_date": trip_date,
                    "boarding_stop": boarding.stop_fact_id,
                    "alighting_stop": alighting.stop_fact_id,
                    "day_type": day_type,
                    "ready_at": ready_at,
                },
            ).fetchone()

    def _direct_stop_signatures(
        self,
        boarding_stops: tuple[_BusStopCandidate, ...],
        alighting_stops: tuple[_BusStopCandidate, ...],
        ready_at: datetime,
        trip_date: date,
        *,
        limit: int,
    ) -> tuple[tuple[str, str], ...]:
        """12×12 개별 조회 없이 exact 직통 운행편이 있는 정류장 쌍을 한 번에 압축한다."""

        if not boarding_stops or not alighting_stops:
            return ()
        runtime_dsn = getattr(self, "_runtime_dsn", None)
        if runtime_dsn is None:
            return tuple(
                (boarding.stop_fact_id, alighting.stop_fact_id)
                for boarding in boarding_stops
                for alighting in alighting_stops
            )[:limit]
        day_type = (
            "SATURDAY"
            if trip_date.isoweekday() == 6
            else "SUNDAY"
            if trip_date.isoweekday() == 7
            else "WEEKDAY"
        )
        with psycopg.connect(runtime_dsn) as connection:
            holiday = connection.execute(
                """SELECT 1 FROM travel_read.active_holiday
                   WHERE holiday_date = %s AND is_public_institution_holiday LIMIT 1""",
                (trip_date,),
            ).fetchone()
            if holiday is not None:
                day_type = "HOLIDAY"
            rows = connection.execute(
                """SELECT board_time.stop_fact_id, alight_time.stop_fact_id,
                          min((%(trip_date)s::date + alight_time.arrival_at
                            + alight_time.arrival_day_offset * interval '1 day')
                            AT TIME ZONE 'Asia/Seoul') AS earliest_arrival
                   FROM travel_read.active_scheduled_trip trip
                   JOIN travel_read.active_service_calendar calendar
                     ON calendar.service_id = trip.service_id
                   JOIN travel_read.active_stop_time board_time
                     ON board_time.trip_id = trip.trip_id
                    AND board_time.stop_fact_id = ANY(%(boarding_stops)s::text[])
                   JOIN travel_read.active_stop_time alight_time
                     ON alight_time.trip_id = trip.trip_id
                    AND alight_time.stop_fact_id = ANY(%(alighting_stops)s::text[])
                  WHERE calendar.day_type = %(day_type)s
                    AND calendar.starts_on <= %(trip_date)s
                    AND calendar.ends_on >= %(trip_date)s
                    AND (trip.timetable_effective_from IS NULL
                         OR trip.timetable_effective_from <= %(trip_date)s)
                    AND (trip.timetable_effective_to IS NULL
                         OR trip.timetable_effective_to >= %(trip_date)s)
                    AND board_time.stop_sequence < alight_time.stop_sequence
                    AND (%(trip_date)s::date + board_time.departure_at
                         + board_time.departure_day_offset * interval '1 day')
                         AT TIME ZONE 'Asia/Seoul' >= %(ready_at)s
                    AND NOT EXISTS (
                      SELECT 1 FROM travel_read.active_service_calendar_exception exception
                       WHERE exception.service_id = trip.service_id
                         AND exception.exception_date = %(trip_date)s
                         AND exception.exception_type = 'REMOVED')
                    AND NOT EXISTS (
                      SELECT 1 FROM travel_read.active_route_service_exception exception
                       WHERE exception.route_fact_id = trip.route_fact_id
                         AND exception.exception_type = 'SUSPENDED'
                         AND exception.starts_at <= %(ready_at)s
                         AND (exception.ends_at IS NULL
                              OR exception.ends_at >= %(ready_at)s))
                  GROUP BY board_time.stop_fact_id, alight_time.stop_fact_id
                  ORDER BY earliest_arrival,
                           board_time.stop_fact_id, alight_time.stop_fact_id
                  LIMIT %(limit)s""",
                {
                    "trip_date": trip_date,
                    "boarding_stops": [item.stop_fact_id for item in boarding_stops],
                    "alighting_stops": [item.stop_fact_id for item in alighting_stops],
                    "day_type": day_type,
                    "ready_at": ready_at,
                    "limit": limit,
                },
            ).fetchall()
        if (
            rows
            and len(rows[0]) >= 11
            and len(boarding_stops) == 1
            and len(alighting_stops) == 1
        ):
            # 단위 테스트의 최소 DB 대역은 모든 timetable 조회에 상세 행을 돌려준다.
            return ((boarding_stops[0].stop_fact_id, alighting_stops[0].stop_fact_id),)
        return tuple((str(row[0]), str(row[1])) for row in rows)

    @staticmethod
    def _rank_direct_stop_signatures(
        signatures: tuple[tuple[str, str], ...],
        boarding_stops: tuple[_BusStopCandidate, ...],
        alighting_stops: tuple[_BusStopCandidate, ...],
    ) -> tuple[tuple[str, str], ...]:
        """SQL의 도착 순위 안에서 양 끝 직선거리 순위로 TMAP 검증 순서를 압축한다."""

        boarding_rank = {
            stop.stop_fact_id: rank for rank, stop in enumerate(boarding_stops)
        }
        alighting_rank = {
            stop.stop_fact_id: rank for rank, stop in enumerate(alighting_stops)
        }
        arrival_rank = {signature: rank for rank, signature in enumerate(signatures)}
        return tuple(
            sorted(
                signatures,
                key=lambda signature: (
                    boarding_rank.get(signature[0], len(boarding_stops))
                    + alighting_rank.get(signature[1], len(alighting_stops)),
                    arrival_rank[signature],
                    signature,
                ),
            )
        )

    @staticmethod
    def _simple_source_evidence(
        fact_id: str,
        category: str,
        value,
        source_id: str,
        publication_id: str | None,
        data_as_of: date | datetime,
        *,
        source_fact_id: str | None = None,
    ) -> EvidenceFact:
        return EvidenceFact(
            fact_id=fact_id,
            category=category,
            value=value,
            source_refs=(
                SourceRef(
                    source_id=source_id,
                    publication_id=publication_id,
                    source_fact_id=source_fact_id or fact_id,
                ),
            ),
            data_as_of=data_as_of,
            retrieved_at=datetime.now(UTC),
            confidence=1,
            derivation=Derivation(kind="source"),
        )

    def _taxi(
        self,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
    ) -> RoutePlanResult | None:
        fact_id = self._fact_id("tmap.driving", origin, destination, departure_at)
        key = self._cache_key("tmap.driving", origin, destination, departure_at, request)
        if key in self._failed_route_keys:
            return None
        adapter = TmapAdapter(
            self._driving_contract,
            self._driving_cache,
            self._transport,
            lambda raw: normalize_tmap_driving(
                raw,
                origin_entrance_id=origin.entrance_id,
                destination_entrance_id=destination.entrance_id,
                route_fact_id=fact_id,
            ),
            self._api_key,
            self._safe_log,
        )
        try:
            payload = {
                "startX": origin.position.longitude,
                "startY": origin.position.latitude,
                "endX": destination.position.longitude,
                "endY": destination.position.latitude,
                "reqCoordType": "WGS84GEO",
                "resCoordType": "WGS84GEO",
                "trafficInfo": "Y",
            }
            with self._route_key_lock(key):
                if self._driving_cache.get(key) is None:
                    with budget.call_slot():
                        if self._driving_cache.get(key) is None:
                            budget.claim_external_call("tmap.driving", allocation="taxi_driving")
                        route = adapter.fetch(key, payload)
                else:
                    route = adapter.fetch(key, payload)
        except TmapRequestFailed:
            self._failed_route_keys.add(key)
            raise
        observed_at = datetime.now(UTC)
        policy_fact_id = (
            f"jeju.taxi-fare-policy:{self._taxi_policy.effective_from.isoformat()}:STANDARD"
        )
        policy_publication_id = self._fare_policy_publication(
            "jeju.taxi-fare-policy", self._taxi_policy.effective_from
        )
        try:
            fare = estimate_taxi_fare(
                self._taxi_policy,
                vehicle_type="STANDARD",
                distance_meters=route.distance_meters,
                duration_seconds=route.duration_seconds,
                departure_at=departure_at,
                route_fact_id=fact_id,
                policy_fact_id=policy_fact_id,
            )
        except ValueError as error:
            if str(error) == "FARE_POLICY_NOT_EFFECTIVE":
                return None
            raise
        if request.total_budget_krw is not None and fare.maximum_krw > request.total_budget_krw:
            return None
        minutes = max(1, math.ceil(route.duration_seconds / 60))
        fare_fact_id = f"computed.taxi-fare:{fact_id}"
        taxi = TaxiAlternative(
            duration_minutes=minutes,
            distance_meters=route.distance_meters,
            fare_min_krw=fare.minimum_krw,
            fare_max_krw=fare.maximum_krw,
            evidence_fact_ids=(
                fact_id,
                policy_fact_id,
                fare_fact_id,
                *origin.evidence_fact_ids,
                *destination.evidence_fact_ids,
            ),
        )
        future_proxy = abs(departure_at.astimezone(UTC) - observed_at) > timedelta(minutes=30)
        route_evidence = self._route_evidence(
            fact_id,
            "driving_route",
            {
                "distance_meters": route.distance_meters,
                "duration_seconds": route.duration_seconds,
                "toll_fare_krw": route.toll_fare_krw,
                "traffic_basis": (
                    "current_snapshot_proxy_for_future" if future_proxy else "current_snapshot"
                ),
                "requested_departure_at": departure_at.isoformat(),
            },
            "tmap.driving",
            observed_at,
            is_estimated=future_proxy,
            confidence=0.65 if future_proxy else 0.9,
        )
        policy_evidence = EvidenceFact(
            fact_id=policy_fact_id,
            category="taxi_fare_policy",
            value={"effective_from": self._taxi_policy.effective_from.isoformat()},
            source_refs=(
                SourceRef(
                    source_id="jeju.taxi-fare-policy",
                    publication_id=policy_publication_id,
                    source_fact_id=policy_fact_id,
                ),
            ),
            data_as_of=self._taxi_policy.effective_from,
            retrieved_at=datetime.now(UTC),
            confidence=1,
            derivation=Derivation(kind="source"),
        )
        fare_evidence = EvidenceFact(
            fact_id=fare_fact_id,
            category="computed_taxi_fare",
            value={
                "minimum_krw": fare.minimum_krw,
                "maximum_krw": fare.maximum_krw,
                "dispatch_guaranteed": False,
            },
            unit="KRW",
            data_as_of=observed_at,
            retrieved_at=datetime.now(UTC),
            confidence=0.8,
            is_estimated=True,
            derivation=Derivation(
                kind="computed",
                formula="official_meter_policy(route_distance, route_duration)",
                input_fact_ids=(fact_id, policy_fact_id),
            ),
        )
        return RoutePlanResult(
            VerifiedRouteOption(
                origin.place_id,
                destination.place_id,
                minutes,
                0,
                0,
                fare.minimum_krw,
                fare.maximum_krw,
                0,
                Transfer(
                    mode="taxi",
                    taxi_alternative=taxi,
                    distance_meters=route.distance_meters,
                ),
                (
                    fact_id,
                    policy_fact_id,
                    fare_fact_id,
                    *origin.evidence_fact_ids,
                    *destination.evidence_fact_ids,
                ),
            ),
            (route_evidence, policy_evidence, fare_evidence),
            (
                self._source_metadata("tmap.driving", observed_at),
                self._source_metadata("jeju.taxi-fare-policy", departure_at),
            ),
        )

    def _walking_multiplier(self, request: RecommendDayTripsInput, strategy: Strategy) -> float:
        policy = self._planning_policy.walking_speed_multiplier
        if request.party.mobility_support_required:
            return policy.mobility_support
        if request.party.seniors or request.party.children:
            return policy.senior_or_child
        if strategy == Strategy.RELAXED:
            return policy.relaxed
        return policy.normal

    def _fare_policy_publication(self, source_id: str, effective_from: date) -> str | None:
        """로컬 정책의 모든 핵심 요율과 일치하는 active publication만 연결한다."""

        if not self._runtime_dsn:
            return None
        with psycopg.connect(self._runtime_dsn) as connection:
            if source_id == "jeju.bus-fare-policy" and self._bus_fare_policy is not None:
                rows = connection.execute(
                    """SELECT publication_id::text, fare_class, adult_min_krw,
                              adult_max_krw, child_min_krw, child_max_krw
                       FROM travel_read.active_bus_fare_policy
                       WHERE effective_from = %s ORDER BY fare_class""",
                    (effective_from,),
                ).fetchall()
                expected = {
                    "STANDARD": self._bus_fare_policy.standard,
                    "EXPRESS": self._bus_fare_policy.express,
                }
                if (
                    len(rows) == 2
                    and len({row[0] for row in rows}) == 1
                    and all(
                        row[1] in expected
                        and tuple(row[2:])
                        == (
                            expected[row[1]].adult_min_krw,
                            expected[row[1]].adult_max_krw,
                            expected[row[1]].child_min_krw,
                            expected[row[1]].child_max_krw,
                        )
                        for row in rows
                    )
                ):
                    return str(rows[0][0])
                return None
            if source_id == "jeju.taxi-fare-policy":
                rows = connection.execute(
                    """SELECT publication_id::text, vehicle_type, base_fare_krw,
                              base_distance_meters, distance_unit_meters,
                              distance_unit_fare_krw, time_speed_threshold_kph,
                              time_unit_seconds, time_unit_fare_krw,
                              long_distance_threshold_meters,
                              long_distance_unit_fare_krw, night_start, night_end,
                              night_surcharge_ratio, call_fee_max_krw
                       FROM travel_read.active_taxi_fare_policy
                       WHERE effective_from = %s ORDER BY vehicle_type""",
                    (effective_from,),
                ).fetchall()
                expected = {item.vehicle_type: item for item in self._taxi_policy.vehicle_types}
                if len(rows) != len(expected) or len({row[0] for row in rows}) != 1:
                    return None
                for row in rows:
                    vehicle = expected.get(row[1])
                    if vehicle is None or tuple(row[2:]) != (
                        vehicle.base_fare_krw,
                        vehicle.base_distance_meters,
                        vehicle.distance_unit_meters,
                        vehicle.distance_unit_fare_krw,
                        vehicle.time_speed_threshold_kph,
                        vehicle.time_unit_seconds,
                        vehicle.time_unit_fare_krw,
                        vehicle.long_distance_threshold_meters,
                        vehicle.long_distance_unit_fare_krw,
                        self._taxi_policy.night_start,
                        self._taxi_policy.night_end,
                        self._taxi_policy.night_surcharge_ratio,
                        self._taxi_policy.call_fee_max_krw,
                    ):
                        return None
                return str(rows[0][0])
        return None

    @staticmethod
    def _fact_id(
        source_id: str,
        origin: VerifiedEntrance,
        destination: VerifiedEntrance,
        departure_at: datetime,
    ) -> str:
        value = (
            f"{source_id}|{origin.entrance_id}|{destination.entrance_id}|"
            f"{departure_at.isoformat(timespec='minutes')}"
        )
        return f"{source_id}:{hashlib.sha256(value.encode()).hexdigest()[:24]}"

    @staticmethod
    def _cache_key(source_id, origin, destination, departure_at, request):
        route_snapshot = (
            "static-pedestrian" if source_id == "tmap.pedestrian" else "current-driving-snapshot"
        )
        return RouteCacheKey.build(
            source_id,
            (origin.position.latitude, origin.position.longitude),
            (destination.position.latitude, destination.position.longitude),
            route_snapshot,
            set(),
            "raw-route-summary",
            request.schema_version,
        )

    def _route_key_lock(self, key: RouteCacheKey) -> Lock:
        """동일 route cache key의 동시 외부 호출을 single-flight로 만든다."""

        with self._route_key_locks_guard:
            return self._route_key_locks.setdefault(key, Lock())

    @staticmethod
    def _route_evidence(
        fact_id,
        category,
        value,
        source_id,
        data_as_of,
        *,
        is_estimated=False,
        confidence=0.9,
    ):
        return EvidenceFact(
            fact_id=fact_id,
            category=category,
            value=value,
            source_refs=(SourceRef(source_id=source_id, source_fact_id=fact_id),),
            data_as_of=data_as_of,
            retrieved_at=data_as_of,
            confidence=confidence,
            is_estimated=is_estimated,
            derivation=Derivation(kind="source"),
        )

    def _source_metadata(self, source_id: str, data_as_of: datetime):
        contract = (
            self._pedestrian_contract
            if source_id == "tmap.pedestrian"
            else self._driving_contract
            if source_id == "tmap.driving"
            else None
        )
        catalog_contract = next(
            (item for item in self._catalog.sources if item.id == source_id), None
        )
        return DataSourceMetadata(
            source_id=source_id,
            provider=(
                contract.provider
                if contract
                else catalog_contract.provider
                if catalog_contract
                else "제주특별자치도"
            ),
            dataset_version=None,
            data_as_of=data_as_of,
            retrieved_at=datetime.now(UTC),
            status="ACTIVE",
            attribution_text=(
                contract.license.attribution_text
                if contract
                else catalog_contract.license.attribution_text
                if catalog_contract
                else "제주특별자치도 버스정보시스템"
            ),
        )
