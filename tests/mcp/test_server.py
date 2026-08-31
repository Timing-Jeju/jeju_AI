"""FastMCP 도구 노출과 실패 응답 테스트."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from pydantic import create_model

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
from jeju_trip.interfaces.mcp.server import create_server
from tests.factories import make_request, make_success_response

TOOL_CONTRACTS = {
    "recommend_jeju_day_trips": (RecommendDayTripsInput, DayTripResponse),
    "evaluate_jeju_day_trip": (EvaluateJejuDayTripInput, EvaluationResponse),
    "revalidate_jeju_day_trip": (RevalidateJejuDayTripInput, RevalidationResponse),
    "search_jeju_places": (SearchPlacesInput, SearchPlacesResponse),
    "inspect_jeju_bus_stop": (InspectBusStopInput, BusStopInspection),
    "preview_jeju_transfer": (PreviewTransferInput, PreviewTransferResponse),
}


class SuccessGateway:
    def recommend(self, request: RecommendDayTripsInput):
        return make_success_response().model_copy(update={"request": request})

    def search_places(self, request: SearchPlacesInput) -> SearchPlacesResponse:
        return SearchPlacesResponse(status="success")

    def inspect_bus_stop(self, request: InspectBusStopInput) -> BusStopInspection:
        return BusStopInspection(status="not_found")

    def preview_transfer(self, request: PreviewTransferInput) -> PreviewTransferResponse:
        return PreviewTransferResponse(status="unavailable")


@pytest.mark.asyncio
async def test_tools_list_exposes_six_input_and_output_schemas() -> None:
    """tools/list가 생성·판정·실시간을 포함한 여섯 도구의 스키마를 노출해야 한다."""

    tools = await create_server().list_tools()
    assert {tool.name for tool in tools} == {
        "recommend_jeju_day_trips",
        "evaluate_jeju_day_trip",
        "revalidate_jeju_day_trip",
        "search_jeju_places",
        "inspect_jeju_bus_stop",
        "preview_jeju_transfer",
    }
    assert all(tool.inputSchema for tool in tools)
    assert all(tool.outputSchema for tool in tools)


@pytest.mark.asyncio
async def test_mcp_schemas_match_pydantic_contracts_without_drift() -> None:
    """여섯 MCP input/output Schema는 Pydantic 생성 결과와 정확히 같아야 한다."""

    tools = await create_server().list_tools()
    for tool in tools:
        input_model, output_model = TOOL_CONTRACTS[tool.name]
        expected_input = create_model(
            f"{tool.name}Arguments", request=(input_model, ...)
        ).model_json_schema()
        assert tool.inputSchema == expected_input
        assert tool.outputSchema == output_model.model_json_schema()


@pytest.mark.asyncio
async def test_mcp_returns_structured_three_route_response() -> None:
    """MCP 추천 도구는 검증된 추천 경로 세 개를 structuredContent로 반환해야 한다."""

    service = TripPlannerService(SuccessGateway())
    result = await create_server(service).call_tool(
        "recommend_jeju_day_trips",
        {"request": make_request().model_dump(mode="json")},
    )
    assert isinstance(result, tuple)
    structured = cast(dict[str, Any], result[1])
    assert structured["status"] == "success"
    assert len(structured["recommendations"]) == 3


def test_insufficient_data_returns_structured_failure() -> None:
    """데이터 부족은 MCP 내부 오류가 아닌 구조화된 전체 실패로 반환되어야 한다."""

    response = TripPlannerService().recommend(make_request())
    assert response.status == "insufficient_feasible_routes"
    assert response.recommendations == ()
    assert response.failure is not None
    assert response.failure.code == "insufficient_feasible_routes"


def test_failure_returns_decision_for_every_requested_place() -> None:
    """추천 전체 실패도 모든 필수·선호 장소의 미검증 결정을 반환해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["required_places"] = ({"place_id": "required-1", "name": "필수 장소"},)
    payload["preferred_places"] = ({"place_id": "preferred-1", "name": "선호 장소"},)
    response = TripPlannerService().recommend(RecommendDayTripsInput.model_validate(payload))
    assert {decision.place_id for decision in response.place_decisions} == {
        "required-1",
        "preferred-1",
    }
    assert all(decision.decision == "unverifiable" for decision in response.place_decisions)


def test_removed_improve_mode_returns_migration_error() -> None:
    """구버전 improve 입력은 판정 도구 안내가 담긴 구조화 오류를 반환해야 한다."""

    request = make_request().model_copy(update={"request_mode": "improve"})
    response = TripPlannerService().recommend(request)
    assert response.failure is not None
    assert response.failure.code == "REQUEST_MODE_REMOVED"


def test_final_json_stays_under_one_mebibyte() -> None:
    """최종 JSON 응답은 1MiB를 넘지 않아야 한다."""

    response = TripPlannerService().recommend(make_request())
    rendered = json.dumps(response.model_dump(mode="json"), ensure_ascii=False).encode()
    assert len(rendered) < 1024 * 1024
