"""보안 Codex entrypoint로 여섯 MCP 도구의 제한된 라이브 인수를 수행한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from jeju_trip.domain.models import (
    AccommodationInput,
    ActivityWindow,
    BusStopInspection,
    DayTripResponse,
    DiscoveryPreferences,
    EvaluationResponse,
    FoodPreferences,
    FullTimelineTransfer,
    InspectBusStopInput,
    Party,
    PreviewTransferInput,
    PreviewTransferResponse,
    ProgressInput,
    Recommendation,
    RecommendDayTripsInput,
    RestPreferences,
    RevalidateJejuDayTripInput,
    RevalidationResponse,
    SearchPlacesInput,
    SearchPlacesResponse,
    TransportPreferences,
    WalkingPreferences,
)
from jeju_trip.planning.timeline_conversion import recommendation_to_full_timeline

ROOT = Path(__file__).resolve().parents[1]
UV_COMMAND = Path("/opt/homebrew/bin/uv")
SERVER_ARGS = (
    "--directory",
    str(ROOT),
    "run",
    "--frozen",
    "--no-env-file",
    "jeju-trip-mcp-codex",
)
KST = ZoneInfo("Asia/Seoul")
TOOL_NAMES = (
    "recommend_jeju_day_trips",
    "evaluate_jeju_day_trip",
    "revalidate_jeju_day_trip",
    "search_jeju_places",
    "inspect_jeju_bus_stop",
    "preview_jeju_transfer",
)


class AcceptanceFailure(RuntimeError):
    """민감한 외부 값 대신 안정적인 reason code만 보존하는 인수 실패."""


class McpSession(Protocol):
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult: ...


def build_representative_request(
    trip_date: date,
    *,
    accommodation_id: str,
    accommodation_name: str,
) -> RecommendDayTripsInput:
    """사용자 원문·GPS 없이 당일 버스 전용 대표 요청을 Pydantic으로 만든다."""

    return RecommendDayTripsInput(
        trip_date=trip_date,
        accommodation=AccommodationInput(
            place_id=accommodation_id,
            name=accommodation_name,
        ),
        activity_window=ActivityWindow(
            start_at=datetime.combine(trip_date, time(7), tzinfo=KST),
            end_at=datetime.combine(trip_date, time(19), tzinfo=KST),
        ),
        party=Party(mobility_support_required=False),
        transport=TransportPreferences(
            allowed_modes={"bus"},
            preferred_mode="bus",
            selection_policy="prefer_selected",
            fallback_order=(),
            max_transfers_per_leg=1,
        ),
        walking=WalkingPreferences(
            allow_direct_walk=False,
            avoid_stairs_required=False,
        ),
        rest=RestPreferences(),
        discovery=DiscoveryPreferences(
            allow_additional_attractions=True,
            maximum_additional_places=3,
        ),
        food=FoodPreferences(auto_schedule_meals=False, auto_schedule_cafe=False),
        original_text=None,
    )


def deduplicate_revalidation_candidates(
    candidates: Iterable[tuple[str, str, str, str]],
) -> tuple[tuple[str, str, str, str], ...]:
    """같은 stop/route 쌍을 중복하지 않고 선택 버스를 최대 세 개로 제한한다."""

    selected: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        _, stop_id, route_id, _ = candidate
        identity = (stop_id, route_id)
        if identity in seen:
            continue
        selected.append(candidate)
        seen.add(identity)
        if len(selected) == 3:
            break
    return tuple(selected)


def _git_commit() -> str:
    environment = {
        key: os.environ[key]
        for key in ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR")
        if os.environ.get(key)
    }
    try:
        result = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if len(commit) == 40 else "unknown"


def _new_report(run_date: date) -> dict[str, Any]:
    return {
        "verdict": "MCP_LIVE_ACCEPTANCE_FAILED",
        "commit": _git_commit(),
        "run_date_kst": run_date.isoformat(),
        "mcp": {
            "transport": "local_stdio",
            "registered_command_match": True,
            "tool_count": 0,
            "schema_status": "not_run",
        },
        "tools": {
            name: {"status": "not_run", "calls": 0}
            for name in TOOL_NAMES
        },
        "external_evidence": {"tmap_coupled": False, "tago_coupled": False},
        "quality": {
            "user_text_used": False,
            "gps_used": False,
            "raw_or_geometry_persisted": False,
        },
        "deferred": [],
        "reason_code": None,
    }


def acceptance_failure_code(error: BaseException) -> str | None:
    """비동기 context가 감싼 예외에서도 안전한 인수 reason code만 찾는다."""

    if isinstance(error, AcceptanceFailure):
        return str(error)
    if isinstance(error, BaseExceptionGroup):
        return next(
            (
                code
                for nested in error.exceptions
                if (code := acceptance_failure_code(nested)) is not None
            ),
            None,
        )
    return None


async def _call_structured(
    session: McpSession,
    report: dict[str, Any],
    tool_name: str,
    request: Any,
) -> dict[str, Any]:
    report["tools"][tool_name]["calls"] += 1
    result = await session.call_tool(
        tool_name,
        {"request": request.model_dump(mode="json")},
    )
    if result.isError or result.structuredContent is None:
        raise AcceptanceFailure("MCP_TOOL_PROTOCOL_ERROR")
    return cast(dict[str, Any], result.structuredContent)


def _select_hotel(response: SearchPlacesResponse):
    candidates = tuple(place for place in response.places if "제주알" in place.name)
    if response.status != "success" or len(candidates) != 1:
        raise AcceptanceFailure("LIVE_HOTEL_NOT_UNIQUELY_IDENTIFIED")
    return candidates[0]


def _validate_generation(response: DayTripResponse) -> Recommendation:
    if response.status != "success":
        if response.recommendations:
            raise AcceptanceFailure("PARTIAL_RECOMMENDATIONS_FORBIDDEN")
        raise AcceptanceFailure("LIVE_AVAILABILITY_INSUFFICIENT_FEASIBLE_ROUTES")
    if len(response.recommendations) != 3:
        raise AcceptanceFailure("LIVE_RECOMMENDATION_COUNT_INVALID")
    if {item.strategy.value for item in response.recommendations} != {
        "balanced",
        "relaxed",
        "experience_max",
    }:
        raise AcceptanceFailure("LIVE_RECOMMENDATION_STRATEGIES_INVALID")
    for recommendation in response.recommendations:
        transfers = tuple(
            event.transfer
            for event in recommendation.timeline
            if event.type == "transfer" and event.transfer is not None
        )
        if not transfers or any(transfer.mode != "bus" for transfer in transfers):
            raise AcceptanceFailure("LIVE_NON_BUS_TRANSFER_SELECTED")
        if recommendation.accommodation_return_at > response.request.activity_window.end_at:
            raise AcceptanceFailure("LIVE_RETURN_WINDOW_EXCEEDED")
    return next(
        item for item in response.recommendations if item.strategy.value == "balanced"
    )


def _first_activity_place_id(recommendation: Recommendation) -> str:
    for event in recommendation.timeline:
        if event.type == "visit" and event.visit is not None:
            return event.visit.place_id
        if event.type == "meal" and event.meal is not None:
            return event.meal.place_id
        if event.type == "rest" and event.rest is not None:
            return event.rest.place_id
    raise AcceptanceFailure("LIVE_FIRST_ACTIVITY_MISSING")


def _bus_candidates(
    recommendation: Recommendation,
) -> tuple[tuple[str, str, str, str], ...]:
    raw = []
    for event in recommendation.timeline:
        if event.transfer is None or event.transfer.mode != "bus":
            continue
        for ride in event.transfer.bus_rides:
            raw.append(
                (
                    event.event_id,
                    ride.canonical_boarding_stop_id,
                    ride.route_id,
                    event.start_at.isoformat(),
                )
            )
    candidates = deduplicate_revalidation_candidates(raw)
    if not candidates:
        raise AcceptanceFailure("LIVE_SELECTED_BUS_MISSING")
    return candidates


def _fact_categories(evaluation: EvaluationResponse) -> list[str]:
    return sorted({fact.category for fact in evaluation.evidence_facts})


def _revalidation_evaluations(response: RevalidationResponse) -> tuple[EvaluationResponse, ...]:
    return tuple(
        item
        for item in (
            response.original_evaluation,
            response.remaining_evaluation,
            *(option.revalidated_evaluation for option in response.recovery_options),
        )
        if item is not None
    )


def _has_coupled_realtime_fact(response: RevalidationResponse) -> bool:
    evaluations = _revalidation_evaluations(response)
    realtime_fact_ids = {
        fact.fact_id
        for evaluation in evaluations
        for fact in evaluation.evidence_facts
        if fact.category == "realtime_bus_arrival"
    }
    if not realtime_fact_ids:
        return False
    return any(
        realtime_fact_ids & set(segment.evidence_fact_ids)
        for evaluation in evaluations
        for segment in evaluation.segment_evaluations
        if segment.mode == "bus"
    )


def _recovery_is_grounded(response: RevalidationResponse) -> bool:
    for option in response.recovery_options:
        has_numeric_claim = any(
            value is not None
            for value in (
                option.expected_duration_minutes,
                option.expected_distance_meters,
                option.expected_cost,
            )
        )
        if has_numeric_claim and not option.evidence_fact_ids:
            return False
    return True


async def run_acceptance_session(
    session: McpSession,
    report: dict[str, Any],
    *,
    now: datetime,
) -> None:
    """초기화된 한 MCP 세션에서 6도구와 Generate→Evaluate→Revalidate를 검증한다."""

    search = SearchPlacesResponse.model_validate(
        await _call_structured(
            session,
            report,
            "search_jeju_places",
            SearchPlacesInput(query="제주알", limit=10),
        )
    )
    hotel = _select_hotel(search)
    report["tools"]["search_jeju_places"].update(
        status="success",
        matched_places=1,
        source_lineage=bool(hotel.source_refs),
    )

    request = build_representative_request(
        now.date(),
        accommodation_id=hotel.place_id,
        accommodation_name=hotel.name,
    )
    generated = DayTripResponse.model_validate(
        await _call_structured(session, report, "recommend_jeju_day_trips", request)
    )
    if generated.status != "success":
        report["tools"]["recommend_jeju_day_trips"].update(
            status=generated.status,
            recommendation_count=len(generated.recommendations),
        )
    balanced = _validate_generation(generated)
    report["tools"]["recommend_jeju_day_trips"].update(
        status="success",
        recommendation_count=3,
        strategies=sorted(item.strategy.value for item in generated.recommendations),
        transfer_modes=["bus"],
        fact_categories=sorted({fact.category for fact in generated.evidence_facts}),
    )

    candidates = _bus_candidates(balanced)
    inspected = BusStopInspection.model_validate(
        await _call_structured(
            session,
            report,
            "inspect_jeju_bus_stop",
            InspectBusStopInput(stop_id=candidates[0][1]),
        )
    )
    if (
        inspected.status != "success"
        or inspected.mapping_status != "CONFIRMED"
        or not inspected.canonical_stop_id
        or not inspected.provider_stop_id
        or not inspected.mapping_method
        or not inspected.route_numbers
        or not inspected.source_refs
    ):
        raise AcceptanceFailure("LIVE_BUS_STOP_INSPECTION_INVALID")
    report["tools"]["inspect_jeju_bus_stop"].update(
        status="success",
        mapping_status="CONFIRMED",
        source_lineage=True,
    )

    preview = PreviewTransferResponse.model_validate(
        await _call_structured(
            session,
            report,
            "preview_jeju_transfer",
            PreviewTransferInput(
                origin_place_id=hotel.place_id,
                destination_place_id=_first_activity_place_id(balanced),
                departure_at=balanced.timeline[0].start_at,
                allowed_modes={"walk", "taxi"},
            ),
        )
    )
    if preview.status != "success" or preview.transfer is None or not preview.evidence_facts:
        raise AcceptanceFailure("LIVE_TRANSFER_PREVIEW_UNAVAILABLE")
    preview_categories = sorted({fact.category for fact in preview.evidence_facts})
    report["tools"]["preview_jeju_transfer"].update(
        status="success",
        transfer_mode=preview.transfer.mode,
        fact_categories=preview_categories,
    )
    report["external_evidence"]["tmap_coupled"] = any(
        source.source_id.startswith("tmap.") for source in preview.data_sources
    )

    full_timeline = recommendation_to_full_timeline(request, balanced)
    evaluated = EvaluationResponse.model_validate(
        await _call_structured(session, report, "evaluate_jeju_day_trip", full_timeline)
    )
    recommendation_bus_segments = sum(
        event.transfer is not None and event.transfer.mode == "bus"
        for event in balanced.timeline
    )
    evaluation_bus_segments = sum(
        segment.mode == "bus" for segment in evaluated.segment_evaluations
    )
    if (
        evaluated.status not in {"feasible", "feasible_with_caution"}
        or not evaluated.schedule_window_fit
        or not evaluated.normalized_schedule.events
        or evaluation_bus_segments != recommendation_bus_segments
    ):
        raise AcceptanceFailure("LIVE_EVALUATION_INVALID")
    if any(
        (
            segment.required_minutes is not None
            or segment.distance_meters is not None
            or segment.cost_krw
        )
        and not segment.evidence_fact_ids
        for segment in evaluated.segment_evaluations
    ):
        raise AcceptanceFailure("LIVE_EVALUATION_NUMERIC_EVIDENCE_MISSING")
    report["tools"]["evaluate_jeju_day_trip"].update(
        status=evaluated.status,
        timing_status=evaluated.timing_status,
        evidence_status=evaluated.evidence_status,
        schedule_window_fit=True,
        bus_segments=evaluation_bus_segments,
        fact_categories=_fact_categories(evaluated),
    )

    if now.date() != full_timeline.trip_date or now >= full_timeline.activity_window.end_at:
        report["tools"]["revalidate_jeju_day_trip"].update(status="deferred")
        report["deferred"].append("LIVE_TAGO_WINDOW_DEFERRED")
        report["verdict"] = "MCP_SETUP_PASS / LIVE_TAGO_WINDOW_DEFERRED"
        return

    for event_id, stop_id, route_id, _ in candidates:
        transfer = next(
            item
            for item in full_timeline.timeline
            if isinstance(item, FullTimelineTransfer) and item.event_id == event_id
        )
        completed = tuple(
            item.event_id for item in full_timeline.timeline if item.end_at <= transfer.start_at
        )
        response = RevalidationResponse.model_validate(
            await _call_structured(
                session,
                report,
                "revalidate_jeju_day_trip",
                RevalidateJejuDayTripInput(
                    checked_at=now,
                    progress=ProgressInput(
                        state="waiting_bus",
                        current_event_id=event_id,
                        completed_event_ids=completed,
                        actual_time=now,
                        current_place_id=transfer.from_place.place_id,
                        current_stop_id=stop_id,
                        current_route_id=route_id,
                    ),
                    itinerary=full_timeline,
                ),
            )
        )
        report["tools"]["revalidate_jeju_day_trip"].update(
            status=response.status,
            timing_status=response.timing_status,
            evidence_status=response.evidence_status,
        )
        if not _recovery_is_grounded(response):
            raise AcceptanceFailure("LIVE_RECOVERY_EVIDENCE_MISSING")
        if _has_coupled_realtime_fact(response):
            report["tools"]["revalidate_jeju_day_trip"].update(
                status=response.status,
                realtime_fact_category="realtime_bus_arrival",
            )
            report["external_evidence"]["tago_coupled"] = True
            report["verdict"] = "MCP_LIVE_ACCEPTANCE_PASS"
            return

    report["tools"]["revalidate_jeju_day_trip"].update(status="deferred")
    report["deferred"].append("LIVE_TAGO_WINDOW_DEFERRED")
    report["verdict"] = "MCP_SETUP_PASS / LIVE_TAGO_WINDOW_DEFERRED"


async def run_live_acceptance() -> tuple[dict[str, Any], int]:
    """등록 명령과 같은 stdio 프로세스를 띄우고 안전한 요약만 반환한다."""

    now = datetime.now(KST)
    report = _new_report(now.date())
    if not UV_COMMAND.is_file():
        report["reason_code"] = "CODEX_UV_COMMAND_MISSING"
        return report, 1
    parameters = StdioServerParameters(
        command=str(UV_COMMAND),
        args=list(SERVER_ARGS),
        cwd=ROOT,
    )
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
            async with (
                stdio_client(parameters, errlog=stderr) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                report["mcp"]["tool_count"] = len(tools.tools)
                if names != set(TOOL_NAMES):
                    raise AcceptanceFailure("MCP_TOOL_SET_INVALID")
                if any(not tool.inputSchema or not tool.outputSchema for tool in tools.tools):
                    raise AcceptanceFailure("MCP_SCHEMA_MISSING")
                report["mcp"]["schema_status"] = "success"
                await run_acceptance_session(session, report, now=now)
            stderr.seek(0)
            stderr_text = stderr.read()
        forbidden_stderr_markers = (
            "Traceback",
            "FeatureCollection",
            "serviceKey=",
            "appKey=",
            "JEJU_TMAP_API_KEY=",
            "JEJU_TAGO_SERVICE_KEY=",
        )
        if any(marker in stderr_text for marker in forbidden_stderr_markers):
            raise AcceptanceFailure("MCP_STDERR_SENSITIVE_OR_TRACEBACK")
    except Exception as error:
        report["reason_code"] = (
            acceptance_failure_code(error) or "MCP_LIVE_ACCEPTANCE_UNEXPECTED_FAILURE"
        )
        return report, 1
    return report, 0


def main() -> int:
    """명시적 실행에서만 live API를 호출하고 비민감 JSON 요약을 출력한다."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="승인된 DB·TMAP·TAGO live 호출을 명시적으로 허용합니다.",
    )
    arguments = parser.parse_args()
    if not arguments.confirm_live:
        print(
            json.dumps(
                {
                    "verdict": "LIVE_CONFIRMATION_REQUIRED",
                    "reason_code": "USE_CONFIRM_LIVE",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    report, exit_code = asyncio.run(run_live_acceptance())
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
