"""장소 ID 순서 제안과 검증 수치 계산을 분리한 결정론적 일정 생성기."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Literal, Protocol, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from jeju_trip.domain.models import (
    Coordinates,
    CostRange,
    DataSourceMetadata,
    DayTripResponse,
    EvidenceFact,
    Failure,
    GroundedReason,
    MealDetails,
    ModeReasonCode,
    PlaceDecision,
    PlanningContext,
    Recommendation,
    RecommendationScore,
    RecommendationSegmentRisk,
    RecommendDayTripsInput,
    RestDetails,
    ScoreComponent,
    ScoreComponentName,
    Strategy,
    TimelineEvent,
    Totals,
    Transfer,
    TransportSelectionSummary,
    ValidationSummary,
    VisitDetails,
)
from jeju_trip.planning.execution_budget import (
    ExecutionBudget,
    PlanningBudgetExceeded,
    PlanningTimeout,
)
from jeju_trip.planning.multi_day import (
    history_load_penalty,
    history_place_policy,
    overnight_rest_below_preference,
    previous_transport_cost_max,
)
from jeju_trip.planning.policy import PlanningPolicy
from jeju_trip.planning.validation import validate_three_diverse_recommendations

KST = ZoneInfo("Asia/Seoul")
GenerationFailureCode = Literal[
    "insufficient_feasible_routes",
    "PLACE_AMBIGUOUS",
    "PLANNING_TIMEOUT",
    "ROUTING_BUDGET_EXHAUSTED",
]


def strategy_transfer_slack_minutes(
    policy: PlanningPolicy | None, strategy: Strategy
) -> int:
    """전략 의미를 버스·도보 수치와 분리된 실행 여유 정책으로 변환한다."""

    if policy is None or strategy is Strategy.EXPERIENCE_MAX:
        return 0
    if strategy is Strategy.RELAXED:
        return policy.risk_slack_minutes.medium_upper_exclusive
    return policy.risk_slack_minutes.high_upper_exclusive


def hard_rest_limit_required(request: RecommendDayTripsInput) -> bool:
    """평가기와 같이 좌석·실내 휴식이 필수일 때만 연속 활동 상한을 하드 제약으로 본다."""

    return any(
        requirement == "required"
        for requirement in (
            request.rest.seat_requirement,
            request.rest.indoor_requirement,
        )
    )


@dataclass(frozen=True)
class VerifiedOpeningWindow:
    opens_at: datetime
    closes_at: datetime
    evidence_fact_ids: tuple[str, ...]
    last_admission_at: datetime | None = None
    last_order_at: datetime | None = None


@dataclass(frozen=True)
class VerifiedGenerationPlace:
    place_id: str
    name: str
    position: Coordinates
    entrance_id: str
    category: str
    opens_at: datetime | None
    closes_at: datetime | None
    last_admission_at: datetime | None
    stay_minutes: int
    is_estimated_stay: bool
    evidence_fact_ids: tuple[str, ...]
    operating_hours_status: Literal["VERIFIED", "UNVERIFIED"] = "VERIFIED"
    activity_type: Literal["visit", "meal", "rest"] = "visit"
    last_order_at: datetime | None = None
    seat: Literal["AVAILABLE", "UNKNOWN"] = "UNKNOWN"
    restroom: Literal["AVAILABLE", "UNKNOWN"] = "UNKNOWN"
    indoor: Literal["AVAILABLE", "UNKNOWN"] = "UNKNOWN"
    opening_windows: tuple[VerifiedOpeningWindow, ...] = ()


@dataclass(frozen=True)
class VerifiedRouteOption:
    from_id: str
    to_id: str
    duration_minutes: int
    walking_minutes: int
    walking_distance_meters: int
    cost_min_krw: int
    cost_max_krw: int
    transfers: int
    transfer: Transfer
    evidence_fact_ids: tuple[str, ...]


class GenerationGateway(Protocol):
    def places(self, request: RecommendDayTripsInput) -> tuple[VerifiedGenerationPlace, ...]: ...

    def route(
        self,
        from_id: str,
        to_id: str,
        departure_at: datetime,
        strategy: Strategy,
        request: RecommendDayTripsInput,
        budget: ExecutionBudget,
    ) -> VerifiedRouteOption | None: ...

    def evidence_facts(self) -> tuple[EvidenceFact, ...]: ...

    def data_sources(self) -> tuple[DataSourceMetadata, ...]: ...


class OrderProposer(Protocol):
    def propose(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[str, ...]]: ...


class CandidateOrderProposer(Protocol):
    def propose_candidates(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[tuple[str, ...], ...]]: ...


class BusOnlyCandidateOrderProvider(Protocol):
    def bus_only_candidate_orders(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[tuple[str, ...], ...]]: ...


class HeuristicOrderProposer:
    """LLM 없이도 장소 ID만으로 bounded 후보 순서를 제안한다."""

    def propose(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[str, ...]]:
        known = {place.place_id for place in places}
        required = tuple(
            item.place_id
            for item in request.required_places
            if item.place_id is not None and item.place_id in known
        )
        preferred = tuple(
            item.place_id
            for item in request.preferred_places
            if item.place_id is not None and item.place_id in known
        )
        return {
            Strategy.BALANCED: (*required, *preferred),
            Strategy.RELAXED: (*required, *preferred[:1]),
            Strategy.EXPERIENCE_MAX: (*required, *reversed(preferred)),
        }


class ScopedTemplateCandidateAssembler:
    """active scope manifest의 candidate group에서 검증 후보를 bounded 조립한다."""

    def __init__(self, gateway: GenerationGateway) -> None:
        self._gateway = gateway

    def propose(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[str, ...]]:
        reader = getattr(self._gateway, "template_candidate_groups", None)
        if reader is None:
            raise CandidateInfeasible("SCOPE_TEMPLATE_UNAVAILABLE")
        groups = reader(request)
        known = {place.place_id for place in places}
        required = tuple(
            item.place_id
            for item in request.required_places
            if item.place_id is not None and item.place_id in known
        )
        result: dict[Strategy, tuple[str, ...]] = {}
        for strategy in Strategy:
            order: list[str] = []
            bounded_groups: list[tuple[str, ...]] = []
            for candidates in groups.get(strategy, ()):
                bounded = tuple(candidate for candidate in candidates if candidate in known)[:2]
                if not bounded:
                    raise CandidateInfeasible("TEMPLATE_CANDIDATE_GROUP_EMPTY")
                bounded_groups.append(bounded)
                order.append(bounded[0])
            missing_required = tuple(place_id for place_id in required if place_id not in order)
            if not missing_required:
                result[strategy] = tuple(order)
                continue
            if len(order) < 3:
                result[strategy] = (*order, *missing_required)
                continue
            rest_candidates = bounded_groups[-1]
            rest_id = (
                rest_candidates[1]
                if strategy is Strategy.RELAXED and len(rest_candidates) > 1
                else order[-1]
            )
            meal_candidates = bounded_groups[1]
            meal_id = (
                meal_candidates[1]
                if strategy is Strategy.EXPERIENCE_MAX and len(meal_candidates) > 1
                else order[1]
            )
            result[strategy] = (order[0], meal_id, *missing_required, rest_id)
        return result


class DynamicClusterCandidateAssembler:
    """전역 cluster 후보에서 전략별 장소 수와 결정적 경로 순서를 만든다."""

    _visit_targets = {
        Strategy.RELAXED: 2,
        Strategy.BALANCED: 3,
        Strategy.EXPERIENCE_MAX: 4,
    }

    def __init__(
        self,
        gateway: GenerationGateway | BusOnlyCandidateOrderProvider | None = None,
    ) -> None:
        self._gateway = gateway

    def propose_candidates(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[tuple[str, ...], ...]]:
        """버스-only는 exact stop-time 탐색 결과를 primary/fallback 순서로 사용한다."""

        if request.transport.allowed_modes == {"bus"} and self._gateway is not None:
            reader = getattr(self._gateway, "bus_only_candidate_orders", None)
            if reader is not None:
                candidates = reader(request, places)
                if any(not candidates.get(strategy) for strategy in Strategy):
                    raise CandidateInfeasible("BUS_ONLY_STRATEGY_ROUTE_UNAVAILABLE")
                return candidates
        return {
            strategy: (order,)
            for strategy, order in self._coordinate_orders(request, places).items()
        }

    def propose(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[str, ...]]:
        return {
            strategy: candidates[0]
            for strategy, candidates in self.propose_candidates(request, places).items()
        }

    def _coordinate_orders(
        self,
        request: RecommendDayTripsInput,
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> dict[Strategy, tuple[str, ...]]:
        visits = tuple(place for place in places if place.activity_type == "visit")
        meals = tuple(place for place in places if place.activity_type == "meal")
        rests = tuple(place for place in places if place.activity_type == "rest")
        required_ids = {
            item.place_id for item in request.required_places if item.place_id is not None
        }
        required_visits = tuple(place for place in visits if place.place_id in required_ids)
        optional_visits = tuple(place for place in visits if place.place_id not in required_ids)
        if request.food.auto_schedule_meals and not meals:
            raise CandidateInfeasible("MEAL_VENUE_UNAVAILABLE")
        if request.food.auto_schedule_cafe and not rests:
            raise CandidateInfeasible("REST_VENUE_UNAVAILABLE")
        origin = request.start_boundary.coordinates
        if origin is None:
            raise CandidateInfeasible("DAY_START_COORDINATES_UNRESOLVED")
        sorted_optional = sorted(
            optional_visits,
            key=lambda place: (self._distance(origin, place.position), place.place_id),
        )
        shared_visit_route = self._two_opt(
            origin,
            [*required_visits, *sorted_optional],
        )
        shared_meal = (
            min(
                meals,
                key=lambda place: (self._distance(origin, place.position), place.place_id),
            )
            if meals
            else None
        )
        shared_rest = (
            min(
                rests,
                key=lambda place: (self._distance(origin, place.position), place.place_id),
            )
            if rests
            else None
        )
        result: dict[Strategy, tuple[str, ...]] = {}
        for strategy in Strategy:
            target = max(self._visit_targets[strategy], len(required_visits))
            needed = max(0, target - len(required_visits))
            optional_ids = {place.place_id for place in sorted_optional[:needed]}
            selected_ids = {
                *(place.place_id for place in required_visits),
                *optional_ids,
            }
            selected_visits = [
                place for place in shared_visit_route if place.place_id in selected_ids
            ]
            if len(selected_visits) < target:
                raise CandidateInfeasible("STRATEGY_VISIT_COUNT_UNAVAILABLE")
            order = [place.place_id for place in selected_visits]
            if request.food.auto_schedule_meals:
                assert shared_meal is not None
                order.insert(min(1, len(order)), shared_meal.place_id)
            if request.food.auto_schedule_cafe:
                assert shared_rest is not None
                order.append(shared_rest.place_id)
            for required in required_ids:
                if required not in order:
                    order.append(required)
            result[strategy] = tuple(dict.fromkeys(order))
        return result

    @staticmethod
    def _distance(left: Coordinates, right: Coordinates) -> float:
        """후보 정렬에만 쓰는 직선거리 하한이다."""

        latitude_scale = 111_320
        longitude_scale = latitude_scale * math.cos(
            math.radians((left.latitude + right.latitude) / 2)
        )
        return math.hypot(
            (left.latitude - right.latitude) * latitude_scale,
            (left.longitude - right.longitude) * longitude_scale,
        )

    def _two_opt(
        self,
        origin: Coordinates,
        places: list[VerifiedGenerationPlace],
    ) -> tuple[VerifiedGenerationPlace, ...]:
        """nearest-neighbor 뒤 2-opt로 숙소 왕복 직선거리 하한을 줄인다."""

        remaining = sorted(places, key=lambda place: place.place_id)
        route: list[VerifiedGenerationPlace] = []
        cursor = origin
        while remaining:
            selected = min(
                remaining,
                key=lambda place: (self._distance(cursor, place.position), place.place_id),
            )
            route.append(selected)
            remaining.remove(selected)
            cursor = selected.position

        def length(items: list[VerifiedGenerationPlace]) -> float:
            points = [origin, *(item.position for item in items), origin]
            return sum(
                self._distance(left, right) for left, right in zip(points, points[1:], strict=False)
            )

        improved = True
        while improved:
            improved = False
            baseline = length(route)
            for start in range(1, max(1, len(route) - 1)):
                for end in range(start + 1, len(route)):
                    candidate = [*route[:start], *reversed(route[start:end]), *route[end:]]
                    candidate_length = length(candidate)
                    if candidate_length + 0.001 < baseline:
                        route = candidate
                        baseline = candidate_length
                        improved = True
        return tuple(route)


class CandidateInfeasible(ValueError):
    """한 전략 후보가 하드 제약을 통과하지 못한 경우."""


class DeterministicDayTripGenerator:
    def __init__(
        self,
        gateway: GenerationGateway,
        proposer: OrderProposer | CandidateOrderProposer,
        planning_policy: PlanningPolicy | None = None,
    ) -> None:
        self._gateway = gateway
        self._proposer = proposer
        self._planning_policy = planning_policy

    @staticmethod
    def _select_opening_window(
        place: VerifiedGenerationPlace, earliest_at: datetime
    ) -> tuple[VerifiedOpeningWindow | None, datetime]:
        """체류 전체를 담는 첫 검증 OPEN 구간을 고르고 휴게 구간을 건너뛴다."""

        windows = place.opening_windows
        if not windows and place.opens_at is not None and place.closes_at is not None:
            windows = (
                VerifiedOpeningWindow(
                    opens_at=place.opens_at,
                    closes_at=place.closes_at,
                    last_admission_at=place.last_admission_at,
                    last_order_at=place.last_order_at,
                    evidence_fact_ids=place.evidence_fact_ids,
                ),
            )
        if not windows:
            return None, earliest_at
        last_order_missed = False
        for window in sorted(windows, key=lambda item: (item.opens_at, item.closes_at)):
            ready_at = max(earliest_at, window.opens_at)
            if window.last_admission_at is not None and ready_at > window.last_admission_at:
                continue
            if (
                place.activity_type == "meal"
                and window.last_order_at is not None
                and ready_at > window.last_order_at
            ):
                if ready_at + timedelta(minutes=place.stay_minutes) <= window.closes_at:
                    last_order_missed = True
                continue
            if ready_at + timedelta(minutes=place.stay_minutes) <= window.closes_at:
                return window, ready_at
        if last_order_missed:
            raise CandidateInfeasible("LAST_ORDER_MISSED")
        raise CandidateInfeasible("OPENING_HOURS_CONFLICT")

    def generate(
        self,
        request: RecommendDayTripsInput,
        *,
        now: datetime | None = None,
        budget: ExecutionBudget | None = None,
    ) -> DayTripResponse:
        generated_at = now or datetime.now(KST)
        execution_budget = budget or ExecutionBudget.generation(
            bus_only=request.transport.allowed_modes == {"bus"}
        )
        resolver = getattr(self._gateway, "resolve_request", None)
        if resolver is not None:
            try:
                request = resolver(request)
            except ValueError as error:
                reason_code = str(error) or "PLACE_UNRESOLVED"
                return self._failure(
                    request,
                    generated_at,
                    "PLACE_AMBIGUOUS"
                    if reason_code == "PLACE_AMBIGUOUS"
                    else "insufficient_feasible_routes",
                    (reason_code,),
                    (),
                )
        available_places = tuple(
            replace(place, stay_minutes=requested)
            if (requested := request.requested_stay_minutes(place.place_id)) is not None
            else place
            for place in self._gateway.places(request)
        )
        history_policy = history_place_policy(request)
        excluded_ids = {
            item.place_id for item in request.excluded_places if item.place_id is not None
        }
        new_meals = {
            place.place_id
            for place in available_places
            if place.activity_type == "meal"
            and place.place_id not in history_policy.avoided_meal_ids
        }
        new_rests = {
            place.place_id
            for place in available_places
            if place.activity_type == "rest"
            and place.place_id not in history_policy.avoided_rest_ids
        }
        reused_meals = set(history_policy.avoided_meal_ids) if not new_meals else set()
        reused_rests = set(history_policy.avoided_rest_ids) if not new_rests else set()
        places = tuple(
            place
            for place in available_places
            if place.place_id not in excluded_ids
            and place.place_id not in history_policy.excluded_visit_ids
            and not (
                request.multi_day.soft_avoid_repeated_meals_and_rests
                and place.activity_type == "meal"
                and new_meals
                and place.place_id in history_policy.avoided_meal_ids
            )
            and not (
                request.multi_day.soft_avoid_repeated_meals_and_rests
                and place.activity_type == "rest"
                and new_rests
                and place.place_id in history_policy.avoided_rest_ids
            )
        )
        place_by_id = {place.place_id: place for place in places}
        required_ids = {
            item.place_id for item in request.required_places if item.place_id is not None
        }
        unresolved_required = tuple(
            item for item in request.required_places if item.place_id not in place_by_id
        )
        if unresolved_required:
            return self._failure(
                request,
                generated_at,
                "insufficient_feasible_routes",
                ("PLACE_UNRESOLVED",),
                places,
            )

        try:
            candidate_reader = getattr(self._proposer, "propose_candidates", None)
            if candidate_reader is None:
                orders = {
                    strategy: (order,)
                    for strategy, order in cast(OrderProposer, self._proposer)
                    .propose(request, places)
                    .items()
                }
            else:
                orders = candidate_reader(request, places)
            with ThreadPoolExecutor(
                max_workers=min(len(Strategy), execution_budget.maximum_concurrency)
            ) as executor:
                futures = tuple(
                    executor.submit(
                        self._schedule_candidates,
                        request,
                        strategy,
                        rank,
                        orders.get(strategy, ()),
                        place_by_id,
                        required_ids,
                        reused_meals,
                        reused_rests,
                        execution_budget,
                    )
                    for rank, strategy in enumerate(Strategy, start=1)
                )
                generated = tuple(future.result() for future in futures)
            generated = self._apply_relative_cost_scores(generated)
            strategy_order = {strategy: index for index, strategy in enumerate(Strategy)}
            recommendations = tuple(
                recommendation.model_copy(update={"rank": rank})
                for rank, recommendation in enumerate(
                    sorted(
                        generated,
                        key=lambda item: (-item.score.total, strategy_order[item.strategy]),
                    ),
                    start=1,
                )
            )
            diversity_failures = validate_three_diverse_recommendations(recommendations)
            if diversity_failures:
                return self._failure(
                    request,
                    generated_at,
                    "insufficient_feasible_routes",
                    diversity_failures,
                    places,
                )
        except CandidateInfeasible as error:
            return self._failure(
                request,
                generated_at,
                "insufficient_feasible_routes",
                (str(error),),
                places,
            )
        except PlanningTimeout:
            return self._failure(
                request, generated_at, "PLANNING_TIMEOUT", ("PLANNING_TIMEOUT",), places
            )
        except PlanningBudgetExceeded:
            return self._failure(
                request,
                generated_at,
                "ROUTING_BUDGET_EXHAUSTED",
                ("ROUTING_BUDGET_EXHAUSTED",),
                places,
            )

        included = {place_id for item in recommendations for place_id in item.place_ids}
        return DayTripResponse(
            request_id=f"req-{uuid4()}",
            generated_at=generated_at,
            status="success",
            planning_context=self._planning_context(request, generated_at),
            request=request,
            recommendations=recommendations,
            evidence_facts=self._gateway.evidence_facts(),
            data_sources=self._gateway.data_sources(),
            place_decisions=self._place_decisions(request, included),
            global_warnings=tuple(
                warning
                for warning, applies in (
                    (
                        "VENUE_UNAVAILABLE",
                        request.food.auto_schedule_meals
                        and all(
                            event.type != "meal"
                            for recommendation in recommendations
                            for event in recommendation.timeline
                        ),
                    ),
                    (
                        "OPENING_HOURS_UNKNOWN",
                        any(
                            place_by_id[place_id].operating_hours_status == "UNVERIFIED"
                            for recommendation in recommendations
                            for place_id in recommendation.place_ids
                        ),
                    ),
                )
                if applies
            ),
            validation=ValidationSummary(
                schema_valid=True,
                timeline_valid=True,
                provenance_valid=True,
                transit_connections_valid=True,
                diversity_valid=True,
                checks=(
                    "REQUIRED_PLACES_INCLUDED",
                    "DAY_BOUNDARY_COMPLETE",
                    "ROUTING_BUDGET_ENFORCED",
                    "NO_PARTIAL_SUCCESS",
                ),
            ),
        )

    def _schedule_candidates(
        self,
        request: RecommendDayTripsInput,
        strategy: Strategy,
        rank: int,
        orders: tuple[tuple[str, ...], ...],
        place_by_id: dict[str, VerifiedGenerationPlace],
        required_ids: set[str],
        reused_meal_ids: set[str],
        reused_rest_ids: set[str],
        budget: ExecutionBudget,
    ) -> Recommendation:
        """primary가 일정 제약에 실패한 전략만 bounded fallback을 시도한다."""

        last_error: CandidateInfeasible | None = None
        for order in orders[:2]:
            try:
                return self._schedule(
                    request,
                    strategy,
                    rank,
                    order,
                    place_by_id,
                    required_ids,
                    reused_meal_ids,
                    reused_rest_ids,
                    budget,
                )
            except CandidateInfeasible as error:
                last_error = error
        raise last_error or CandidateInfeasible("STRATEGY_CANDIDATE_UNAVAILABLE")

    def _schedule(
        self,
        request: RecommendDayTripsInput,
        strategy: Strategy,
        rank: int,
        order: tuple[str, ...],
        place_by_id: dict[str, VerifiedGenerationPlace],
        required_ids: set[str],
        reused_meal_ids: set[str],
        reused_rest_ids: set[str],
        budget: ExecutionBudget,
    ) -> Recommendation:
        """19시 복귀가 넘은 경우에만 optional 체류 단축·제거 후 전 구간을 재탐색한다."""

        place_by_id = self._strategy_place_durations(
            request, strategy, order, place_by_id, required_ids
        )
        try:
            return self._schedule_once(
                request,
                strategy,
                rank,
                order,
                place_by_id,
                required_ids,
                reused_meal_ids,
                reused_rest_ids,
                budget,
            )
        except CandidateInfeasible as error:
            if str(error) != "HOTEL_RETURN_BUFFER_INSUFFICIENT":
                raise
        repaired_places = dict(place_by_id)
        optional = [
            place_id
            for place_id in reversed(order)
            if place_id not in required_ids and place_by_id[place_id].activity_type == "visit"
        ]
        for place_id in optional:
            place = repaired_places[place_id]
            if request.requested_stay_minutes(place_id) is not None:
                continue
            minimum = self._minimum_stay_minutes(place)
            if minimum >= place.stay_minutes:
                continue
            repaired_places[place_id] = replace(place, stay_minutes=minimum)
            try:
                return self._schedule_once(
                    request,
                    strategy,
                    rank,
                    order,
                    repaired_places,
                    required_ids,
                    reused_meal_ids,
                    reused_rest_ids,
                    budget,
                )
            except CandidateInfeasible as error:
                if str(error) != "HOTEL_RETURN_BUFFER_INSUFFICIENT":
                    raise
        reduced_order = list(order)
        for place_id in optional:
            reduced_order.remove(place_id)
            try:
                return self._schedule_once(
                    request,
                    strategy,
                    rank,
                    tuple(reduced_order),
                    repaired_places,
                    required_ids,
                    reused_meal_ids,
                    reused_rest_ids,
                    budget,
                )
            except CandidateInfeasible as error:
                if str(error) != "HOTEL_RETURN_BUFFER_INSUFFICIENT":
                    raise
        raise CandidateInfeasible("HOTEL_RETURN_BUFFER_INSUFFICIENT")

    def _strategy_place_durations(
        self,
        request: RecommendDayTripsInput,
        strategy: Strategy,
        order: tuple[str, ...],
        place_by_id: dict[str, VerifiedGenerationPlace],
        required_ids: set[str],
    ) -> dict[str, VerifiedGenerationPlace]:
        """experience_max의 마지막 휴식을 버전 정책의 최대 체류로 확장한다."""

        if strategy is not Strategy.EXPERIENCE_MAX or self._planning_policy is None:
            return place_by_id
        selected_id = next(
            (
                place_id
                for place_id in reversed(order)
                if place_id in place_by_id
                and place_by_id[place_id].activity_type == "rest"
            ),
            None,
        )
        if selected_id is None:
            selected_id = next(
                (
                    place_id
                    for place_id in reversed(order)
                    if place_id not in required_ids
                    and place_id in place_by_id
                    and place_by_id[place_id].activity_type == "visit"
                ),
                None,
            )
            if selected_id is None:
                return place_by_id
        selected = place_by_id[selected_id]
        if request.requested_stay_minutes(selected_id) is not None:
            return place_by_id
        key = (
            "cafe"
            if selected.activity_type == "rest"
            else {
                "12": "viewpoint_or_beach",
                "14": "museum_or_exhibition",
                "15": "experience_or_theme",
                "28": "experience_or_theme",
                "38": "market_or_shopping",
            }.get(selected.category, "viewpoint_or_beach")
        )
        maximum = self._planning_policy.stay_minutes[key].maximum
        if maximum <= selected.stay_minutes:
            return place_by_id
        return {**place_by_id, selected_id: replace(selected, stay_minutes=maximum)}

    def _minimum_stay_minutes(self, place: VerifiedGenerationPlace) -> int:
        if self._planning_policy is None:
            return place.stay_minutes
        key = (
            "cafe"
            if place.activity_type == "rest"
            else "restaurant"
            if place.activity_type == "meal"
            else {
                "12": "viewpoint_or_beach",
                "14": "museum_or_exhibition",
                "15": "experience_or_theme",
                "28": "experience_or_theme",
                "38": "market_or_shopping",
            }.get(place.category, "viewpoint_or_beach")
        )
        return self._planning_policy.stay_minutes[key].minimum

    def _schedule_once(
        self,
        request: RecommendDayTripsInput,
        strategy: Strategy,
        rank: int,
        order: tuple[str, ...],
        place_by_id: dict[str, VerifiedGenerationPlace],
        required_ids: set[str],
        reused_meal_ids: set[str],
        reused_rest_ids: set[str],
        budget: ExecutionBudget,
    ) -> Recommendation:
        budget.ensure_time_remaining()
        if not order or not required_ids.issubset(order) or len(order) != len(set(order)):
            raise CandidateInfeasible("REQUIRED_PLACE_MISSING")
        if any(place_id not in place_by_id for place_id in order):
            raise CandidateInfeasible("PROPOSED_PLACE_UNKNOWN")
        start_place_id = request.start_boundary.place_id
        end_place_id = request.end_boundary.place_id
        if start_place_id is None or end_place_id is None:
            raise CandidateInfeasible("DAY_BOUNDARY_UNRESOLVED")

        timeline: list[TimelineEvent] = []
        sequence = 1
        current_id = start_place_id
        current_at = request.activity_window.start_at
        walking_minutes = 0
        walking_distance = 0
        cost_min = 0
        cost_max = 0
        transfers = 0
        taxi_cost_max = 0
        bus_cost_min = bus_cost_max = 0
        taxi_cost_min = 0
        used_fact_ids: list[str] = []
        meal_scheduled = False
        continuous_minutes = 0
        transfer_slack_minutes = strategy_transfer_slack_minutes(
            self._planning_policy, strategy
        )
        transfer_slack_fact_id = (
            self._policy_fact_id(
                "risk_slack_minutes.medium_upper_exclusive"
                if strategy is Strategy.RELAXED
                else "risk_slack_minutes.high_upper_exclusive"
            )
            if transfer_slack_minutes
            else None
        )

        for place_id in order:
            place = place_by_id[place_id]
            route = self._gateway.route(current_id, place_id, current_at, strategy, request, budget)
            if route is None:
                raise CandidateInfeasible("ROUTE_EVIDENCE_MISSING")
            self._validate_route(request, route)
            mode_decision = route.transfer.mode_decision
            pickup_minutes = (
                mode_decision.taxi_pickup_buffer_minutes
                if route.transfer.mode == "taxi" and mode_decision is not None
                else None
            )
            if pickup_minutes:
                pickup_end = current_at + timedelta(minutes=pickup_minutes)
                timeline.append(
                    TimelineEvent(
                        event_id=f"{strategy.value}-taxi-pickup-{sequence}",
                        sequence=sequence,
                        type="buffer",
                        start_at=current_at,
                        end_at=pickup_end,
                        duration_minutes=pickup_minutes,
                        title="택시 호출 계획 대기",
                        place_id=current_id,
                        reason_code="TAXI_PICKUP_PLANNING_BUFFER_APPLIED",
                        evidence_fact_ids=(
                            mode_decision.evidence_fact_ids if mode_decision is not None else ()
                        ),
                    )
                )
                sequence += 1
                current_at = pickup_end
            driving_minutes = route.duration_minutes - (pickup_minutes or 0)
            if driving_minutes <= 0:
                raise CandidateInfeasible("TAXI_DRIVING_DURATION_INVALID")
            transfer_end = current_at + timedelta(minutes=driving_minutes)
            timeline.append(
                TimelineEvent(
                    event_id=f"{strategy.value}-transfer-{sequence}",
                    sequence=sequence,
                    type="transfer",
                    start_at=current_at,
                    end_at=transfer_end,
                    duration_minutes=driving_minutes,
                    title=f"{place.name} 이동",
                    transfer=route.transfer,
                    evidence_fact_ids=route.evidence_fact_ids,
                )
            )
            sequence += 1
            current_at = transfer_end
            if transfer_slack_minutes:
                slack_end = current_at + timedelta(minutes=transfer_slack_minutes)
                timeline.append(
                    TimelineEvent(
                        event_id=f"{strategy.value}-safety-buffer-{sequence}",
                        sequence=sequence,
                        type="buffer",
                        start_at=current_at,
                        end_at=slack_end,
                        duration_minutes=transfer_slack_minutes,
                        title="이동 후 일정 여유",
                        place_id=place_id,
                        reason_code="PLANNED_SAFETY_BUFFER",
                        evidence_fact_ids=(
                            (transfer_slack_fact_id,)
                            if transfer_slack_fact_id is not None
                            else route.evidence_fact_ids
                        ),
                    )
                )
                sequence += 1
                current_at = slack_end
            earliest_at = current_at
            if place.activity_type == "meal" and self._planning_policy is not None:
                lunch_start = datetime.combine(
                    request.trip_date,
                    self._planning_policy.meal_windows.lunch_start,
                    tzinfo=earliest_at.tzinfo,
                )
                earliest_at = max(earliest_at, lunch_start)
            selected_window, window_ready_at = self._select_opening_window(
                place, earliest_at
            )
            if window_ready_at > current_at:
                wait_minutes = int((window_ready_at - current_at).total_seconds() // 60)
                timeline.append(
                    TimelineEvent(
                        event_id=f"{strategy.value}-buffer-{sequence}",
                        sequence=sequence,
                        type="buffer",
                        start_at=current_at,
                        end_at=window_ready_at,
                        duration_minutes=wait_minutes,
                        title="운영·식사 시간창 전 대기",
                        place_id=place_id,
                        reason_code="OPERATING_OR_MEAL_WINDOW_WAIT",
                        evidence_fact_ids=place.evidence_fact_ids,
                    )
                )
                sequence += 1
            current_at = window_ready_at
            visit_end = current_at + timedelta(minutes=place.stay_minutes)
            if place.activity_type == "meal":
                if self._planning_policy is not None:
                    lunch_end = datetime.combine(
                        request.trip_date,
                        self._planning_policy.meal_windows.lunch_end,
                        tzinfo=current_at.tzinfo,
                    )
                    if visit_end > lunch_end:
                        raise CandidateInfeasible("MEAL_WINDOW_MISSED")
                activity = TimelineEvent(
                    event_id=f"{strategy.value}-meal-{sequence}",
                    sequence=sequence,
                    type="meal",
                    start_at=current_at,
                    end_at=visit_end,
                    duration_minutes=place.stay_minutes,
                    title=place.name,
                    place_id=place_id,
                    meal=MealDetails(
                        place_id=place_id,
                        venue_name=place.name,
                        position=place.position,
                        entrance_id=place.entrance_id,
                        opens_at=(selected_window.opens_at if selected_window else None),
                        closes_at=(selected_window.closes_at if selected_window else None),
                        last_order_at=(
                            selected_window.last_order_at if selected_window else None
                        ),
                        operating_hours_status=place.operating_hours_status,
                        evidence_fact_ids=place.evidence_fact_ids,
                    ),
                    evidence_fact_ids=place.evidence_fact_ids,
                    issue_codes=(("REPEAT_MEAL_FALLBACK",) if place_id in reused_meal_ids else ()),
                )
                meal_scheduled = True
                continuous_minutes = 0
            elif place.activity_type == "rest":
                activity = TimelineEvent(
                    event_id=f"{strategy.value}-rest-{sequence}",
                    sequence=sequence,
                    type="rest",
                    start_at=current_at,
                    end_at=visit_end,
                    duration_minutes=place.stay_minutes,
                    title=place.name,
                    place_id=place_id,
                    rest=RestDetails(
                        place_id=place_id,
                        venue_name=place.name,
                        position=place.position,
                        entrance_id=place.entrance_id,
                        opens_at=(selected_window.opens_at if selected_window else None),
                        closes_at=(selected_window.closes_at if selected_window else None),
                        operating_hours_status=place.operating_hours_status,
                        seat=place.seat,
                        restroom=place.restroom,
                        indoor=place.indoor,
                        evidence_fact_ids=place.evidence_fact_ids,
                    ),
                    evidence_fact_ids=place.evidence_fact_ids,
                    issue_codes=(("REPEAT_REST_FALLBACK",) if place_id in reused_rest_ids else ()),
                )
                continuous_minutes = 0
            else:
                activity = TimelineEvent(
                    event_id=f"{strategy.value}-visit-{sequence}",
                    sequence=sequence,
                    type="visit",
                    start_at=current_at,
                    end_at=visit_end,
                    duration_minutes=place.stay_minutes,
                    title=place.name,
                    place_id=place_id,
                    visit=VisitDetails(
                        place_id=place_id,
                        name=place.name,
                        position=place.position,
                        entrance_id=place.entrance_id,
                        arrival_at=current_at,
                        entry_at=current_at,
                        departure_at=visit_end,
                        stay_minutes=place.stay_minutes,
                        operating_hours_status=place.operating_hours_status,
                        opens_at=(selected_window.opens_at if selected_window else None),
                        closes_at=(selected_window.closes_at if selected_window else None),
                        last_admission_at=(
                            selected_window.last_admission_at if selected_window else None
                        ),
                        evidence_fact_ids=place.evidence_fact_ids,
                    ),
                    evidence_fact_ids=place.evidence_fact_ids,
                )
            timeline.append(activity)
            sequence += 1
            current_at = visit_end
            current_id = place_id
            if place.activity_type not in {"meal", "rest"}:
                continuous_minutes += route.duration_minutes + place.stay_minutes
            walking_minutes += route.walking_minutes
            walking_distance += route.walking_distance_meters
            cost_min += route.cost_min_krw
            cost_max += route.cost_max_krw
            if route.transfer.mode == "taxi":
                taxi_cost_min += route.cost_min_krw
                taxi_cost_max += route.cost_max_krw
            elif route.transfer.mode == "bus":
                bus_cost_min += route.cost_min_krw
                bus_cost_max += route.cost_max_krw
            transfers += route.transfers
            used_fact_ids.extend((*route.evidence_fact_ids, *place.evidence_fact_ids))

        if request.food.auto_schedule_meals and not meal_scheduled:
            raise CandidateInfeasible("MEAL_VENUE_UNAVAILABLE")
        if request.food.auto_schedule_cafe and not any(event.type == "rest" for event in timeline):
            raise CandidateInfeasible("REST_VENUE_UNAVAILABLE")
        if (
            hard_rest_limit_required(request)
            and continuous_minutes > request.rest.max_continuous_activity_minutes
        ):
            raise CandidateInfeasible("REST_VENUE_UNAVAILABLE")

        return_route = self._gateway.route(
            current_id, end_place_id, current_at, strategy, request, budget
        )
        if return_route is None:
            raise CandidateInfeasible("HOTEL_RETURN_ROUTE_MISSING")
        self._validate_route(request, return_route)
        return_mode_decision = return_route.transfer.mode_decision
        return_pickup_minutes = (
            return_mode_decision.taxi_pickup_buffer_minutes
            if return_route.transfer.mode == "taxi" and return_mode_decision is not None
            else None
        )
        if return_pickup_minutes:
            pickup_end = current_at + timedelta(minutes=return_pickup_minutes)
            timeline.append(
                TimelineEvent(
                    event_id=f"{strategy.value}-taxi-pickup-{sequence}",
                    sequence=sequence,
                    type="buffer",
                    start_at=current_at,
                    end_at=pickup_end,
                    duration_minutes=return_pickup_minutes,
                    title="택시 호출 계획 대기",
                    place_id=current_id,
                    reason_code="TAXI_PICKUP_PLANNING_BUFFER_APPLIED",
                    evidence_fact_ids=(
                        return_mode_decision.evidence_fact_ids
                        if return_mode_decision is not None
                        else ()
                    ),
                )
            )
            sequence += 1
            current_at = pickup_end
        return_driving_minutes = return_route.duration_minutes - (return_pickup_minutes or 0)
        if return_driving_minutes <= 0:
            raise CandidateInfeasible("TAXI_DRIVING_DURATION_INVALID")
        return_end = current_at + timedelta(minutes=return_driving_minutes)
        if return_end + timedelta(minutes=20) > request.activity_window.end_at:
            raise CandidateInfeasible("HOTEL_RETURN_BUFFER_INSUFFICIENT")
        timeline.append(
            TimelineEvent(
                event_id=f"{strategy.value}-transfer-{sequence}",
                sequence=sequence,
                type="transfer",
                start_at=current_at,
                end_at=return_end,
                duration_minutes=return_driving_minutes,
                title="하루 종료 장소 도착",
                transfer=return_route.transfer,
                evidence_fact_ids=return_route.evidence_fact_ids,
            )
        )
        walking_minutes += return_route.walking_minutes
        walking_distance += return_route.walking_distance_meters
        cost_min += return_route.cost_min_krw
        cost_max += return_route.cost_max_krw
        if return_route.transfer.mode == "taxi":
            taxi_cost_min += return_route.cost_min_krw
            taxi_cost_max += return_route.cost_max_krw
        elif return_route.transfer.mode == "bus":
            bus_cost_min += return_route.cost_min_krw
            bus_cost_max += return_route.cost_max_krw
        transfers += return_route.transfers
        used_fact_ids.extend(return_route.evidence_fact_ids)

        if walking_distance > request.walking.max_total_distance_meters:
            raise CandidateInfeasible("TOTAL_WALKING_LIMIT_EXCEEDED")
        if cost_max > (request.total_budget_krw or cost_max):
            raise CandidateInfeasible("TOTAL_BUDGET_EXCEEDED")
        if (
            request.multi_day.trip_transport_budget_krw is not None
            and previous_transport_cost_max(request) + cost_max
            > request.multi_day.trip_transport_budget_krw
        ):
            raise CandidateInfeasible("TRIP_TRANSPORT_BUDGET_EXCEEDED")

        by_type = {
            event_type: sum(
                event.duration_minutes for event in timeline if event.type == event_type
            )
            for event_type in ("visit", "transfer", "rest", "meal", "buffer")
        }
        total_minutes = sum(event.duration_minutes for event in timeline)
        reason_fact_ids = tuple(dict.fromkeys(used_fact_ids))
        if not reason_fact_ids:
            raise CandidateInfeasible("EVIDENCE_FACT_MISSING")
        score = self._score(
            request=request,
            strategy=strategy,
            order=order,
            transfer_minutes=by_type["transfer"],
            walking_distance=walking_distance,
            cost_max=cost_max,
            evidence_fact_ids=reason_fact_ids,
        )
        segment_risks = []
        risk_order = {"low": 0, "medium": 1, "high": 2}
        for index, event in enumerate(timeline):
            if event.type != "transfer":
                continue
            if index == len(timeline) - 1:
                slack = int((request.activity_window.end_at - event.end_at).total_seconds() // 60)
            else:
                cursor = index + 1
                slack = 0
                while cursor < len(timeline) and timeline[cursor].type == "buffer":
                    slack += timeline[cursor].duration_minutes
                    cursor += 1
            high_threshold = (
                self._planning_policy.risk_slack_minutes.high_upper_exclusive
                if self._planning_policy is not None
                else 10
            )
            medium_threshold = (
                self._planning_policy.risk_slack_minutes.medium_upper_exclusive
                if self._planning_policy is not None
                else 20
            )
            risk = (
                "low"
                if slack >= medium_threshold
                else "medium"
                if slack >= high_threshold
                else "high"
            )
            segment_risks.append(
                RecommendationSegmentRisk(
                    event_id=event.event_id,
                    risk=risk,
                    slack_minutes=slack,
                    evidence_fact_ids=event.evidence_fact_ids,
                )
            )
        overall_risk = max(
            (item.risk for item in segment_risks),
            key=lambda value: risk_order.get(value, 3),
            default="low",
        )
        bus_distance = sum(
            event.transfer.distance_meters
            for event in timeline
            if event.transfer is not None and event.transfer.mode == "bus"
        )
        taxi_distance = sum(
            event.transfer.distance_meters
            for event in timeline
            if event.transfer is not None and event.transfer.mode == "taxi"
        )
        return Recommendation(
            route_id=f"route-{strategy.value}",
            rank=rank,
            strategy=strategy,
            title=f"{strategy.value} 제주 하루 일정",
            score=score,
            recommendation_reasons=(
                GroundedReason(
                    text=(
                        "문-to-문 이동 근거 안에서 하루 종료 장소에 도착하며 "
                        "운영시간은 재확인이 필요합니다."
                        if any(
                            place_by_id[place_id].operating_hours_status == "UNVERIFIED"
                            for place_id in order
                        )
                        else "검증된 운영시간과 문-to-문 이동 근거 안에서 "
                        "하루 종료 장소에 도착합니다."
                    ),
                    evidence_fact_ids=reason_fact_ids,
                ),
            ),
            tradeoffs=(f"총 환승 {transfers}회",),
            place_ids=order,
            feasibility=(
                "feasible_with_caution"
                if any(
                    place_by_id[place_id].operating_hours_status == "UNVERIFIED"
                    for place_id in order
                )
                else "feasible"
            ),
            overall_risk=cast(Literal["high", "medium", "low"], overall_risk),
            segment_risks=tuple(segment_risks),
            revalidate_at=(
                request.activity_window.start_at - timedelta(days=7),
                request.activity_window.start_at - timedelta(days=1),
                request.activity_window.start_at - timedelta(hours=2),
            ),
            required_place_ids_included=tuple(
                place_id for place_id in order if place_id in required_ids
            ),
            preferred_place_ids_included=tuple(
                item.place_id
                for item in request.preferred_places
                if item.place_id is not None and item.place_id in order
            ),
            preferred_place_ids_excluded=tuple(
                item.place_id
                for item in request.preferred_places
                if item.place_id is not None and item.place_id not in order
            ),
            day_start_at=timeline[0].start_at,
            day_end_at=timeline[-1].end_at,
            start_place_id=start_place_id,
            end_place_id=end_place_id,
            accommodation_departure_at=timeline[0].start_at,
            accommodation_return_at=timeline[-1].end_at,
            timeline=tuple(timeline),
            totals=Totals(
                total_minutes=total_minutes,
                visit_minutes=by_type["visit"],
                transfer_minutes=by_type["transfer"],
                rest_minutes=by_type["rest"],
                meal_minutes=by_type["meal"],
                buffer_minutes=by_type["buffer"],
                walking_minutes=walking_minutes,
                walking_distance_meters=walking_distance,
                taxi_pickup_buffer_minutes=sum(
                    event.duration_minutes
                    for event in timeline
                    if event.type == "buffer"
                    and event.reason_code == "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
                ),
                bus_wait_minutes=sum(
                    event.transfer.mode_decision.bus_wait_minutes or 0
                    for event in timeline
                    if event.transfer is not None
                    and event.transfer.mode == "bus"
                    and event.transfer.mode_decision is not None
                ),
                estimated_cost=CostRange(
                    min_krw=cost_min,
                    max_krw=cost_max,
                    is_estimated=cost_min != cost_max,
                ),
                bus_distance_meters=bus_distance,
                taxi_distance_meters=taxi_distance,
                total_distance_meters=walking_distance + bus_distance + taxi_distance,
                bus_cost=CostRange(
                    min_krw=bus_cost_min,
                    max_krw=bus_cost_max,
                    is_estimated=bus_cost_min != bus_cost_max,
                ),
                taxi_cost=CostRange(
                    min_krw=taxi_cost_min,
                    max_krw=taxi_cost_max,
                    is_estimated=bool(taxi_cost_max),
                ),
                derivation_evidence_fact_ids=reason_fact_ids,
            ),
            transport_selection_summary=self._transport_selection_summary(tuple(timeline)),
            issue_codes=tuple(
                code
                for code, applies in (
                    ("REPEAT_MEAL_FALLBACK", bool(set(order) & reused_meal_ids)),
                    ("REPEAT_REST_FALLBACK", bool(set(order) & reused_rest_ids)),
                    ("OVERNIGHT_REST_BELOW_PREFERENCE", overnight_rest_below_preference(request)),
                )
                if applies
            ),
        )

    @staticmethod
    def _apply_relative_cost_scores(
        recommendations: tuple[Recommendation, ...],
    ) -> tuple[Recommendation, ...]:
        costs = [item.totals.estimated_cost.max_krw for item in recommendations]
        low, high = min(costs), max(costs)
        updated = []
        for recommendation, cost in zip(recommendations, costs, strict=True):
            value = 100.0 if high == low else 100 * (high - cost) / (high - low)
            component = recommendation.score.components["cost_efficiency"]
            replacement = component.model_copy(
                update={
                    "value": round(value, 2),
                    "weighted_value": round(value * component.weight / 100, 2),
                }
            )
            components = {**recommendation.score.components, "cost_efficiency": replacement}
            score = recommendation.score.model_copy(
                update={
                    "components": components,
                    "total": round(sum(item.weighted_value for item in components.values()), 2),
                }
            )
            updated.append(recommendation.model_copy(update={"score": score}))
        return tuple(updated)

    def _score(
        self,
        *,
        request: RecommendDayTripsInput,
        strategy: Strategy,
        order: tuple[str, ...],
        transfer_minutes: int,
        walking_distance: int,
        cost_max: int,
        evidence_fact_ids: tuple[str, ...],
    ) -> RecommendationScore:
        if self._planning_policy is None:
            raise CandidateInfeasible("SCORING_POLICY_MISSING")
        weights = self._planning_policy.strategy_score_weights[strategy.value]
        score_policy_fact_id = self._policy_fact_id(f"score.weights.{strategy.value}")
        if score_policy_fact_id is None:
            raise CandidateInfeasible("SCORING_POLICY_FACT_MISSING")
        score_evidence = (score_policy_fact_id, *evidence_fact_ids)
        preferred_ids = {
            item.place_id for item in request.preferred_places if item.place_id is not None
        }
        preferred_score = (
            100.0
            if not preferred_ids
            else 100 * len(preferred_ids & set(order)) / len(preferred_ids)
        )
        window_minutes = max(
            1,
            int(
                (request.activity_window.end_at - request.activity_window.start_at).total_seconds()
                // 60
            ),
        )
        travel_score = max(0.0, 100 * (1 - transfer_minutes / window_minutes))
        walking_limit = max(1, request.walking.max_total_distance_meters)
        comfort_score = max(0.0, 100 * (1 - walking_distance / walking_limit))
        comfort_score = max(
            0.0,
            comfort_score
            - history_load_penalty(
                request,
                walking_distance_meters=walking_distance,
                transfer_minutes=transfer_minutes,
            )
            - (10.0 if overnight_rest_below_preference(request) else 0.0),
        )
        cost_score = (
            max(0.0, 100 * (1 - cost_max / request.total_budget_krw))
            if request.total_budget_krw
            else 100.0
        )
        values: dict[ScoreComponentName, float] = {
            "preferred_places": preferred_score,
            "travel_efficiency": travel_score,
            "reliability": 100.0,
            "comfort": comfort_score,
            "cost_efficiency": cost_score,
            "data_confidence": 100.0,
        }
        components: dict[ScoreComponentName, ScoreComponent] = {}
        for key, value in values.items():
            components[key] = ScoreComponent(
                value=round(value, 2),
                weight=getattr(weights, key),
                weighted_value=round(value * getattr(weights, key) / 100, 2),
                evidence_fact_ids=score_evidence,
            )
        return RecommendationScore(
            total=round(sum(item.weighted_value for item in components.values()), 2),
            components=components,
        )

    @staticmethod
    def _transport_selection_summary(
        timeline: tuple[TimelineEvent, ...],
    ) -> TransportSelectionSummary:
        selected = {"walk": 0, "bus": 0, "taxi": 0}
        feasible_bus = 0
        unverifiable_bus = 0
        near_threshold: list[str] = []
        rejection_counts: dict[ModeReasonCode, int] = {}
        aliases = {
            "TAXI_BUS_TIME_PENALTY_EXCEEDS_30_MINUTES": (ModeReasonCode.BUS_EXTRA_TIME_EXCEEDED),
            "TAXI_BUS_SAVINGS_BELOW_5000_KRW": (ModeReasonCode.BUS_SAVINGS_BELOW_THRESHOLD),
        }
        for event in timeline:
            transfer = event.transfer
            if transfer is None:
                continue
            selected[transfer.mode] += 1
            decision = transfer.mode_decision
            if decision is not None:
                if (
                    decision.bus_wait_minutes is not None
                    and abs(decision.bus_wait_threshold_minutes - decision.bus_wait_minutes) <= 5
                ) or (
                    decision.bus_extra_minutes is not None
                    and abs(decision.max_bus_extra_minutes - decision.bus_extra_minutes) <= 5
                ):
                    near_threshold.append(event.event_id)
                bus_codes = decision.unselected_reason_codes.get("bus", ())
                if decision.selected_mode != "bus":
                    bus_codes = (*bus_codes, *decision.reason_codes)
                for code in bus_codes:
                    try:
                        reason = ModeReasonCode(code)
                    except ValueError:
                        reason = aliases.get(code)
                        if reason is None:
                            continue
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            alternatives = transfer.alternatives
            feasible_bus += sum(
                alternative.mode == "bus" and alternative.status == "feasible"
                for alternative in alternatives
            )
            unverifiable_bus += sum(
                alternative.mode == "bus" and alternative.status == "unverifiable"
                for alternative in alternatives
            )
            for alternative in alternatives:
                if alternative.mode != "bus" or alternative.selected:
                    continue
                for code in alternative.reason_codes:
                    try:
                        reason = ModeReasonCode(code)
                    except ValueError:
                        continue
                    rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        return TransportSelectionSummary(
            selected_walk_legs=selected["walk"],
            selected_bus_legs=selected["bus"],
            selected_taxi_legs=selected["taxi"],
            feasible_bus_alternatives=feasible_bus,
            unverifiable_bus_alternatives=unverifiable_bus,
            near_threshold_legs=tuple(dict.fromkeys(near_threshold)),
            bus_rejection_counts=rejection_counts,
        )

    @staticmethod
    def _validate_route(request: RecommendDayTripsInput, route: VerifiedRouteOption) -> None:
        if route.transfer.mode not in request.transport.allowed_modes:
            raise CandidateInfeasible("MODE_NOT_ALLOWED")
        if route.transfers > request.transport.max_transfers_per_leg:
            raise CandidateInfeasible("TRANSFER_LIMIT_EXCEEDED")
        transfer = route.transfer
        walks = tuple(
            walk
            for walk in (
                transfer.direct_walk,
                transfer.access_walk,
                *transfer.transfer_walks,
                transfer.egress_walk,
            )
            if walk is not None
        )
        for walk in walks:
            limit = (
                request.walking.max_access_walk_minutes
                if transfer.mode == "bus" and walk.kind != "direct_walk"
                else request.walking.max_single_leg_minutes
            )
            measured_minutes = (
                walk.expected_minutes
                if transfer.mode == "bus" and walk.kind != "direct_walk"
                else walk.planned_minutes
            )
            if measured_minutes > limit:
                raise CandidateInfeasible("WALKING_LIMIT_EXCEEDED")
            if request.walking.avoid_stairs_required and walk.stairs_status != "CLEAR":
                raise CandidateInfeasible("ACCESSIBILITY_UNVERIFIABLE")

    def _policy_fact_id(self, policy_key: str) -> str | None:
        resolver = getattr(self._gateway, "policy_fact_id", None)
        return resolver(policy_key) if resolver is not None else None

    def _failure(
        self,
        request: RecommendDayTripsInput,
        generated_at: datetime,
        code: GenerationFailureCode,
        reason_codes: tuple[str, ...],
        places: tuple[VerifiedGenerationPlace, ...],
    ) -> DayTripResponse:
        known_ids = {place.place_id for place in places}
        return DayTripResponse(
            request_id=f"req-{uuid4()}",
            generated_at=generated_at,
            status="insufficient_feasible_routes",
            planning_context=self._planning_context(request, generated_at),
            request=request,
            evidence_facts=self._gateway.evidence_facts(),
            data_sources=self._gateway.data_sources(),
            place_decisions=tuple(
                PlaceDecision(
                    place_id=item.place_id,
                    requested_priority=cast(Literal["required", "preferred", "excluded"], priority),
                    decision=(
                        "excluded"
                        if priority == "excluded"
                        else "unverifiable"
                        if item.place_id not in known_ids
                        else "excluded"
                    ),
                    reason_codes=reason_codes,
                )
                for priority, items in (
                    ("required", request.required_places),
                    ("preferred", request.preferred_places),
                    ("excluded", request.excluded_places),
                )
                for item in items
            ),
            validation=ValidationSummary(
                schema_valid=True,
                timeline_valid=False,
                provenance_valid=True,
                transit_connections_valid=False,
                diversity_valid="ROUTES_NOT_DIVERSE" not in reason_codes,
                checks=("NO_PARTIAL_SUCCESS",),
            ),
            failure=Failure(
                code=code,
                message="검증된 서로 다른 세 일정을 만들 수 없습니다.",
                reason_codes=reason_codes,
            ),
        )

    @staticmethod
    def _planning_context(
        request: RecommendDayTripsInput, generated_at: datetime
    ) -> PlanningContext:
        trip_start = datetime.combine(request.trip_date, time.min, tzinfo=KST)
        return PlanningContext(
            planned_at=generated_at,
            trip_date=request.trip_date,
            days_before_trip=max(0, (request.trip_date - generated_at.date()).days),
            schedule_basis="service_calendar",
            plan_expires_at=min(trip_start, generated_at + timedelta(days=1)),
        )

    @staticmethod
    def _place_decisions(
        request: RecommendDayTripsInput, included: set[str]
    ) -> tuple[PlaceDecision, ...]:
        return tuple(
            PlaceDecision(
                place_id=item.place_id,
                requested_priority=cast(Literal["required", "preferred", "excluded"], priority),
                decision=(
                    "excluded"
                    if priority == "excluded"
                    else "included"
                    if item.place_id in included
                    else "excluded"
                ),
                reason_codes=(
                    ()
                    if item.place_id in included
                    else ("USER_EXCLUDED",)
                    if priority == "excluded"
                    else ("NOT_SELECTED_BY_STRATEGY",)
                ),
            )
            for priority, items in (
                ("required", request.required_places),
                ("preferred", request.preferred_places),
                ("excluded", request.excluded_places),
            )
            for item in items
        )
