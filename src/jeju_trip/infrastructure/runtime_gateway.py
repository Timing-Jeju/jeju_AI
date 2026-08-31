"""MCP 공개 도구를 active publication과 일시적 route adapter에 연결한다."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal, cast

from jeju_trip.domain.models import (
    AccommodationInput,
    ActivityWindow,
    BusStopInspection,
    DayTripResponse,
    DiscoveryPreferences,
    FoodPreferences,
    InspectBusStopInput,
    PreviewTransferInput,
    PreviewTransferResponse,
    RecommendDayTripsInput,
    SearchPlacesInput,
    SearchPlacesResponse,
    Strategy,
    TransportPreferences,
)
from jeju_trip.infrastructure.read_repository import ActiveTravelReadRepository
from jeju_trip.infrastructure.runtime_generation_gateway import PostgresGenerationGateway
from jeju_trip.infrastructure.runtime_routing import TmapDoorRoutePlanner
from jeju_trip.planning.execution_budget import ExecutionBudget
from jeju_trip.planning.generation import (
    DeterministicDayTripGenerator,
    DynamicClusterCandidateAssembler,
)
from jeju_trip.planning.policy import PlanningPolicy


class RuntimePlanningGateway:
    """요청별 evidence ledger를 만들고 읽기 도구는 repository에 위임한다."""

    def __init__(
        self,
        runtime_dsn: str,
        repository: ActiveTravelReadRepository,
        route_planner: TmapDoorRoutePlanner | None,
        planning_policy: PlanningPolicy,
    ) -> None:
        self._runtime_dsn = runtime_dsn
        self._repository = repository
        self._route_planner = route_planner
        self._planning_policy = planning_policy

    def recommend(self, request: RecommendDayTripsInput) -> DayTripResponse | None:
        if self._route_planner is None:
            return None
        gateway = PostgresGenerationGateway(
            self._runtime_dsn,
            self._route_planner,
            self._planning_policy,
        )
        return DeterministicDayTripGenerator(
            gateway,
            DynamicClusterCandidateAssembler(gateway),
            self._planning_policy,
        ).generate(request)

    def search_places(self, request: SearchPlacesInput) -> SearchPlacesResponse:
        return self._repository.search_places(request)

    def inspect_bus_stop(self, request: InspectBusStopInput) -> BusStopInspection:
        return self._repository.inspect_bus_stop(request)

    def preview_transfer(self, request: PreviewTransferInput) -> PreviewTransferResponse:
        if self._route_planner is None:
            return PreviewTransferResponse(
                status="unavailable", reason_code="TMAP_ADAPTER_NOT_CONFIGURED"
            )
        allowed = set(request.allowed_modes)
        preferred = next((mode for mode in ("bus", "taxi", "walk") if mode in allowed), "walk")
        routing_request = RecommendDayTripsInput(
            trip_date=request.departure_at.date(),
            accommodation=AccommodationInput(place_id=request.origin_place_id, name="출발 장소"),
            activity_window=ActivityWindow(
                start_at=request.departure_at,
                end_at=request.departure_at + timedelta(hours=1),
            ),
            transport=TransportPreferences(
                allowed_modes=allowed,
                preferred_mode=cast(Literal["walk", "bus", "taxi"], preferred),
                selection_policy="cost_time_balance",
                fallback_order=tuple(
                    mode
                    for mode in ("taxi", "walk", "bus")
                    if mode in allowed and mode != preferred
                ),
            ),
            discovery=DiscoveryPreferences(
                allow_additional_attractions=False,
                maximum_additional_places=0,
            ),
            food=FoodPreferences(auto_schedule_meals=False, auto_schedule_cafe=False),
        )
        gateway = PostgresGenerationGateway(
            self._runtime_dsn, self._route_planner, self._planning_policy
        )
        option = gateway.route(
            request.origin_place_id,
            request.destination_place_id,
            request.departure_at,
            Strategy.BALANCED,
            routing_request,
            ExecutionBudget.evaluation(),
        )
        if option is None:
            return PreviewTransferResponse(
                status="unavailable", reason_code="ROUTE_EVIDENCE_MISSING"
            )
        return PreviewTransferResponse(
            status="success",
            transfer=option.transfer,
            evidence_facts=gateway.evidence_facts(),
            data_sources=gateway.data_sources(),
        )
