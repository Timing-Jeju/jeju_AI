"""제주 하루 여행 로컬 stdio MCP server."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from jeju_trip.application.preparation import RequestPreparation
from jeju_trip.application.readiness import PostgresCapabilityReadiness
from jeju_trip.application.service import TripPlannerService
from jeju_trip.domain.models import (
    BusStopInspection,
    DayTripResponse,
    EvaluateJejuDayTripInput,
    EvaluationResponse,
    InspectBusStopInput,
    PreviewTransferInput,
    PreviewTransferResponse,
    RecommendDayTripsInput,
    RevalidateJejuDayTripInput,
    RevalidationResponse,
    SearchPlacesInput,
    SearchPlacesResponse,
)
from jeju_trip.infrastructure.public_data_http import BoundedPublicDataClient
from jeju_trip.infrastructure.read_repository import ActiveTravelReadRepository
from jeju_trip.infrastructure.realtime_bus_adapter import RealtimeBusArrivalAdapter
from jeju_trip.infrastructure.realtime_evidence import TagoRealtimeEvidenceProvider
from jeju_trip.infrastructure.runtime_evaluation import PostgresEvaluationEvidenceFactory
from jeju_trip.infrastructure.runtime_gateway import RuntimePlanningGateway
from jeju_trip.infrastructure.runtime_routing import TmapDoorRoutePlanner
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.infrastructure.tmap_adapter import HttpxJsonTransport
from jeju_trip.planning.policy import (
    load_bus_fare_policy,
    load_planning_policy,
    load_taxi_fare_policy,
)

ROOT = Path(__file__).resolve().parents[4]


def _build_runtime_service(
    stack: ExitStack,
    client_factory: Callable[..., httpx.Client],
) -> TripPlannerService:
    runtime_dsn = os.getenv("JEJU_RUNTIME_DSN")
    if runtime_dsn:
        repository = ActiveTravelReadRepository(runtime_dsn)
        planning_policy = load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml")
        api_key = os.getenv("JEJU_TMAP_API_KEY")
        catalog = SourceCatalog.load(ROOT / "config/data_sources.toml")
        if api_key:
            tmap_client = client_factory(timeout=10)
            stack.callback(tmap_client.close)
            route_planner = TmapDoorRoutePlanner(
                catalog,
                HttpxJsonTransport(tmap_client),
                api_key,
                planning_policy,
                load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
                runtime_dsn=runtime_dsn,
                bus_fare_policy=load_bus_fare_policy(
                    ROOT / "config/policies/jeju_bus_fare_2026-08-10.toml"
                ),
            )
        else:
            route_planner = None
        runtime_flags = {
            "walking_routing_ready": route_planner is not None,
            "driving_routing_ready": route_planner is not None,
            "future_bus_planning_ready": route_planner is not None,
            "restaurant_recommendation_ready": False,
            "cafe_recommendation_ready": False,
            "realtime_bus_ready": bool(os.getenv("JEJU_TAGO_SERVICE_KEY")),
        }
        if runtime_flags["realtime_bus_ready"]:
            tago_client = client_factory(timeout=5)
            stack.callback(tago_client.close)
            realtime_provider = TagoRealtimeEvidenceProvider(
                None,
                RealtimeBusArrivalAdapter(BoundedPublicDataClient(tago_client)),
                catalog.require("tago.bus-arrival"),
                repository,
                {"JEJU_TAGO_SERVICE_KEY": os.environ["JEJU_TAGO_SERVICE_KEY"]},
            )
        else:
            realtime_provider = None
        return TripPlannerService(
            RuntimePlanningGateway(
                runtime_dsn,
                repository,
                route_planner,
                planning_policy,
            ),
            readiness=PostgresCapabilityReadiness(repository, runtime_flags),
            evaluation_evidence_factory=(
                PostgresEvaluationEvidenceFactory(runtime_dsn, route_planner, planning_policy)
                if route_planner is not None
                else None
            ),
            realtime_evidence_provider=realtime_provider,
            request_preparation=RequestPreparation(repository),
        )
    return TripPlannerService()


@contextmanager
def managed_service(
    *, client_factory: Callable[..., httpx.Client] = httpx.Client
) -> Iterator[TripPlannerService]:
    """실제 MCP 프로세스의 외부 client를 모든 종료 경로에서 닫는다."""

    with ExitStack() as stack:
        yield _build_runtime_service(stack, client_factory)


def create_server(
    service: TripPlannerService | None = None,
    *,
    fastmcp_options: Mapping[str, Any] | None = None,
) -> FastMCP:
    planner = service or TripPlannerService()
    server = FastMCP(
        "jeju-day-trip-planner",
        instructions=(
            "제주 전역의 생성·사전 판정·실시간 재판정 도구입니다. "
            "검증된 세 경로가 없으면 전체 실패합니다."
        ),
        **dict(fastmcp_options or {}),
    )

    @server.tool()
    def recommend_jeju_day_trips(request: RecommendDayTripsInput) -> DayTripResponse:
        """검증된 balanced·relaxed·experience_max 하루 일정을 정확히 세 개 추천한다."""

        return planner.recommend(request)

    @server.tool()
    def evaluate_jeju_day_trip(request: EvaluateJejuDayTripInput) -> EvaluationResponse:
        """정확한 활동 일정 또는 전체 타임라인을 공식 근거로 판정한다."""

        return planner.evaluate(request)

    @server.tool()
    def revalidate_jeju_day_trip(request: RevalidateJejuDayTripInput) -> RevalidationResponse:
        """현재 진행상태와 선택적 위치로 남은 일정의 위험을 다시 판정한다."""

        return planner.revalidate(request)

    @server.tool()
    def search_jeju_places(request: SearchPlacesInput) -> SearchPlacesResponse:
        """활성 publication의 제주 장소와 검증된 출입구 보유 여부를 검색한다."""

        return planner.search_places(request)

    @server.tool()
    def inspect_jeju_bus_stop(request: InspectBusStopInput) -> BusStopInspection:
        """활성 정류장의 provider ID, 방향, canonical mapping 근거를 조회한다."""

        return planner.inspect_bus_stop(request)

    @server.tool()
    def preview_jeju_transfer(request: PreviewTransferInput) -> PreviewTransferResponse:
        """검증된 입구와 정류장 사이의 이동 연결을 영속화 없이 미리 본다."""

        return planner.preview_transfer(request)

    return server


def main() -> None:
    with managed_service() as service:
        create_server(service).run(transport="stdio")


if __name__ == "__main__":
    main()
