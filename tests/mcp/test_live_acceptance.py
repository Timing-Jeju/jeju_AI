"""명시적 MCP live 인수 실행기의 안전한 요청·보고 경계 테스트."""

from __future__ import annotations

import json
from datetime import date

from scripts.run_live_mcp_acceptance import (
    ROOT,
    SERVER_ARGS,
    TOOL_NAMES,
    UV_COMMAND,
    AcceptanceFailure,
    _new_report,
    acceptance_failure_code,
    build_representative_request,
    deduplicate_revalidation_candidates,
)


def test_live_request_uses_bus_only_without_text_or_gps() -> None:
    """대표 live 요청은 당일 버스 전용이고 사용자 원문·fallback을 포함하지 않아야 한다."""

    request = build_representative_request(
        date(2026, 8, 31),
        accommodation_id="safe-hotel-id",
        accommodation_name="제주알호텔",
    )
    assert request.transport.allowed_modes == {"bus"}
    assert request.transport.preferred_mode == "bus"
    assert request.transport.fallback_order == ()
    assert request.transport.max_transfers_per_leg == 1
    assert request.original_text is None
    assert request.party.mobility_support_required is False
    assert request.walking.avoid_stairs_required is False
    assert request.food.auto_schedule_meals is False
    assert request.food.auto_schedule_cafe is False
    assert request.discovery.maximum_additional_places == 3


def test_live_revalidation_deduplicates_stop_route_pairs_and_limits_calls() -> None:
    """TAGO live 재검증은 같은 stop/route를 제외하고 최대 세 후보만 선택해야 한다."""

    candidates = deduplicate_revalidation_candidates(
        (
            ("event-1", "stop-1", "route-1", "time-1"),
            ("event-1-copy", "stop-1", "route-1", "time-2"),
            ("event-2", "stop-2", "route-2", "time-3"),
            ("event-3", "stop-3", "route-3", "time-4"),
            ("event-4", "stop-4", "route-4", "time-5"),
        )
    )
    assert candidates == (
        ("event-1", "stop-1", "route-1", "time-1"),
        ("event-2", "stop-2", "route-2", "time-3"),
        ("event-3", "stop-3", "route-3", "time-4"),
    )


def test_live_command_matches_codex_registration_command() -> None:
    """live 인수 서버 명령은 Codex에 등록할 절대 경로·frozen 인자와 같아야 한다."""

    assert UV_COMMAND.as_posix() == "/opt/homebrew/bin/uv"
    assert (
        "--directory",
        str(ROOT),
        "run",
        "--frozen",
        "--no-env-file",
        "jeju-trip-mcp-codex",
    ) == SERVER_ARGS


def test_live_report_shape_cannot_store_sensitive_route_details() -> None:
    """live 보고서 기본 구조에는 좌표·정류장 ID·원본 응답·비밀값 필드가 없어야 한다."""

    rendered = json.dumps(_new_report(date(2026, 8, 31)), ensure_ascii=False)
    assert all(name in rendered for name in TOOL_NAMES)
    for forbidden in (
        "latitude",
        "longitude",
        "coordinates",
        "stop_id",
        "route_id",
        "api_key",
        "dsn",
        "raw_response",
        "original_text",
    ):
        assert forbidden not in rendered.casefold()
    assert '"raw_or_geometry_persisted": false' in rendered


def test_live_failure_code_survives_async_exception_group() -> None:
    """구조화 live 실패는 비동기 ExceptionGroup에 감싸져도 unexpected로 바뀌지 않아야 한다."""

    error = ExceptionGroup(
        "task group",
        (RuntimeError("safe"), AcceptanceFailure("LIVE_EXPECTED_FAILURE")),
    )
    assert acceptance_failure_code(error) == "LIVE_EXPECTED_FAILURE"
