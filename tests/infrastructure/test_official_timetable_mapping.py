"""공식 운행행과 TAGO route pattern의 전역 exact mapping 테스트."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date

import pytest

from jeju_trip.infrastructure.official_timetable_mapping import (
    TimetableRouteStop,
    build_exact_mapping_manifest,
    map_exact_weekday_trips,
    merge_incremental_mapping_manifest,
    merge_mapping_manifest_overlay,
)
from jeju_trip.infrastructure.timetable_xlsx import (
    TimetableWorkbookRejected,
    WorkbookCellRow,
)


def _stop(pattern: str, sequence: int, name: str) -> TimetableRouteStop:
    return TimetableRouteStop(
        provider_route_pattern_id=pattern,
        route_number="101",
        provider_stop_id=f"{pattern}-stop-{sequence}",
        route_sequence=sequence,
        direction_text="0",
        latitude=33.4 + sequence / 100,
        longitude=126.5 + sequence / 100,
        stop_name=name,
    )


def _workbook(values: tuple[object, ...], sheet_name: str = "101 출발-도착"):
    return {
        "405001.xlsx": (
            WorkbookCellRow(sheet_name, 5, (None, "(시행일 : 2024.8.1.)")),
            WorkbookCellRow(sheet_name, 7, (None, "구분", "출발", "분기A", "도착")),
            WorkbookCellRow(sheet_name, 8, values),
        )
    }


def test_unique_ordered_checkpoint_selects_one_route_pattern() -> None:
    """같은 노선번호라도 시각 checkpoint 순서가 유일한 TAGO pattern만 선택해야 한다."""

    route_stops = (
        _stop("pattern-a", 1, "출발정류장"),
        _stop("pattern-a", 2, "분기A"),
        _stop("pattern-a", 3, "도착정류장"),
        _stop("pattern-b", 1, "출발정류장"),
        _stop("pattern-b", 2, "분기B"),
        _stop("pattern-b", 3, "도착정류장"),
    )

    result = map_exact_weekday_trips(
        _workbook((None, 1, "09:00", "09:20", "09:40")),
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert len(result.mappings) == 1
    assert result.mappings[0].provider_route_pattern_id == "pattern-a"
    assert [item.provider_stop_id for item in result.mappings[0].stop_times] == [
        "pattern-a-stop-1",
        "pattern-a-stop-2",
        "pattern-a-stop-3",
    ]


def test_tied_route_patterns_are_rejected_instead_of_guessed() -> None:
    """두 pattern이 같은 수의 checkpoint와 일치하면 provider ID를 임의 선택하지 않아야 한다."""

    route_stops = (
        _stop("pattern-a", 1, "출발정류장"),
        _stop("pattern-a", 2, "도착정류장"),
        _stop("pattern-b", 1, "출발정류장"),
        _stop("pattern-b", 2, "도착정류장"),
    )

    result = map_exact_weekday_trips(
        _workbook((None, 1, "09:00", "X", "09:40")),
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert result.mappings == ()
    assert [issue.reason_code for issue in result.issues] == [
        "TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE"
    ]


def test_unique_exact_checkpoint_names_resolve_containment_tie() -> None:
    """checkpoint 수가 같아도 정규화 이름이 정확히 일치하는 pattern이 하나면 확정해야 한다."""

    route_stops = (
        _stop("pattern-exact", 1, "출발"),
        _stop("pattern-exact", 2, "도착"),
        _stop("pattern-contained", 1, "출발정류장"),
        _stop("pattern-contained", 2, "도착정류장"),
    )

    result = map_exact_weekday_trips(
        _workbook((None, 1, "09:00", "X", "09:40")),
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert len(result.mappings) == 1
    assert result.mappings[0].provider_route_pattern_id == "pattern-exact"


def test_unique_full_endpoint_pattern_resolves_equal_checkpoint_count() -> None:
    """동률 후보 중 시각 checkpoint가 실제 시작·종점을 모두 덮는 pattern이 하나면 확정해야 한다."""

    route_stops = (
        _stop("pattern-full", 1, "출발정류장"),
        _stop("pattern-full", 2, "도착정류장"),
        _stop("pattern-partial", 1, "차고지"),
        _stop("pattern-partial", 2, "출발정류장"),
        _stop("pattern-partial", 3, "도착정류장"),
    )

    result = map_exact_weekday_trips(
        _workbook((None, 1, "09:00", "X", "09:40")),
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert len(result.mappings) == 1
    assert result.mappings[0].provider_route_pattern_id == "pattern-full"


def test_official_sheet_direction_resolves_remaining_pattern_tie() -> None:
    """시각 수와 시작·종점이 동률이면 공식 sheet 방향 waypoint 순서가 유일할 때만 확정해야 한다."""

    route_stops = (
        _stop("pattern-via-a", 1, "공항출발정류장"),
        _stop("pattern-via-a", 2, "조천"),
        _stop("pattern-via-a", 3, "남원도착정류장"),
        _stop("pattern-via-b", 1, "공항출발정류장"),
        _stop("pattern-via-b", 2, "김녕"),
        _stop("pattern-via-b", 3, "남원도착정류장"),
    )

    result = map_exact_weekday_trips(
        _workbook(
            (None, 1, "09:00", "X", "09:40"),
            sheet_name="101 공항-조천-남원",
        ),
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert len(result.mappings) == 1
    assert result.mappings[0].provider_route_pattern_id == "pattern-via-a"


def test_official_sheet_direction_keeps_equal_title_matches_unresolved() -> None:
    """공식 sheet 방향 waypoint까지 같은 두 pattern은 임의로 하나를 선택하지 않아야 한다."""

    route_stops = (
        _stop("pattern-a", 1, "공항출발정류장"),
        _stop("pattern-a", 2, "조천"),
        _stop("pattern-a", 3, "남원도착정류장"),
        _stop("pattern-b", 1, "공항출발정류장"),
        _stop("pattern-b", 2, "조천"),
        _stop("pattern-b", 3, "남원도착정류장"),
    )

    result = map_exact_weekday_trips(
        _workbook(
            (None, 1, "09:00", "X", "09:40"),
            sheet_name="101 공항-조천-남원",
        ),
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert result.mappings == ()
    assert result.issues[0].reason_code == "TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE"


def test_explicit_route_number_column_overrides_shared_sheet_title() -> None:
    """여러 노선이 섞인 sheet는 행의 노선번호 열을 exact route number로 사용해야 한다."""

    workbook = {
        "405048.xlsx": (
            WorkbookCellRow("300 도심급행", 5, (None, "(시행일 : 2024.8.1.)")),
            WorkbookCellRow(
                "300 도심급행",
                7,
                (None, "구분", "노선번호", "출발", "도착"),
            ),
            WorkbookCellRow(
                "300 도심급행",
                8,
                (None, 1, "101번", "09:00", "09:40"),
            ),
        )
    }
    route_stops = (
        _stop("pattern-a", 1, "출발정류장"),
        _stop("pattern-a", 2, "도착정류장"),
    )

    result = map_exact_weekday_trips(
        workbook,
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert result.mappings[0].route_number == "101"


def test_midnight_rollover_is_preserved_as_service_day_offset() -> None:
    """23시 이후 0시 checkpoint는 다음 서비스일 24시 표기로 보존해야 한다."""

    route_stops = (
        _stop("pattern-a", 1, "출발정류장"),
        _stop("pattern-a", 2, "분기A"),
        _stop("pattern-a", 3, "도착정류장"),
    )

    result = map_exact_weekday_trips(
        _workbook((None, 1, "23:50", "00:05", "00:20")),
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert [item.arrival_time for item in result.mappings[0].stop_times] == [
        "2350",
        "2405",
        "2420",
    ]


def test_non_weekday_demand_and_multi_clock_rows_are_separate_issues() -> None:
    """휴일·호출형·복수시각 행은 서로 다른 미지원 사유로 집계해야 한다."""

    workbook = {
        "406039.xlsx": (
            WorkbookCellRow(
                "101 (휴일) 출발-도착",
                5,
                (None, "(시행일 : 2024.8.1.)"),
            ),
            WorkbookCellRow("101 (휴일) 출발-도착", 7, (None, "구분", "출발", "도착")),
            WorkbookCellRow("101 (휴일) 출발-도착", 8, (None, 1, "09:00", "09:40")),
            WorkbookCellRow(
                "101 (평일) 출발-도착",
                5,
                (None, "(시행일 : 2024.8.1.)"),
            ),
            WorkbookCellRow("101 (평일) 출발-도착", 7, (None, "구분", "출발", "도착")),
            WorkbookCellRow(
                "101 (평일) 출발-도착",
                8,
                (None, 1, "09:00", "14:00 ~ 21:30 (실시간 호출형)"),
            ),
            WorkbookCellRow(
                "101 (평일) 출발-도착",
                9,
                (None, 2, "09:00\n(09:10)", "09:40"),
            ),
        )
    }

    result = map_exact_weekday_trips(
        workbook,
        (_stop("pattern-a", 1, "출발"), _stop("pattern-a", 2, "도착")),
        target_date=date(2026, 8, 18),
    )

    assert Counter(issue.reason_code for issue in result.issues) == Counter(
        {
            "TIMETABLE_NON_WEEKDAY_ROW_EXCLUDED": 1,
            "TIMETABLE_DEMAND_RESPONSIVE_ROW_UNSUPPORTED": 1,
            "TIMETABLE_MULTIPLE_CLOCKS_PER_CELL_UNSUPPORTED": 1,
        }
    )


def test_effective_date_is_required_and_preserved_without_invention() -> None:
    """시행일이 없는 운행행은 제외하고 명시된 시행일만 mapping에 보존해야 한다."""

    sheet_name = "101 출발-도착"
    rows = (
        WorkbookCellRow(sheet_name, 7, (None, "구분", "출발", "도착")),
        WorkbookCellRow(sheet_name, 8, (None, 1, "09:00", "09:40")),
    )
    route_stops = (_stop("pattern-a", 1, "출발"), _stop("pattern-a", 2, "도착"))

    missing = map_exact_weekday_trips(
        {"405001.xlsx": rows},
        route_stops,
        target_date=date(2026, 8, 18),
    )
    explicit = map_exact_weekday_trips(
        {
            "405001.xlsx": (
                WorkbookCellRow(sheet_name, 5, (None, "(시행일: '25.8.14.)")),
                *rows,
            )
        },
        route_stops,
        target_date=date(2026, 8, 18),
    )

    assert missing.mappings == ()
    assert missing.issues[0].reason_code == "TIMETABLE_EFFECTIVE_DATE_MISSING"
    assert explicit.mappings[0].timetable_effective_from == date(2025, 8, 14)
    assert explicit.mappings[0].timetable_effective_to == date(2026, 8, 18)


def test_future_effective_date_does_not_cover_earlier_target_date() -> None:
    """목표일보다 늦게 시행되는 시간표를 과거 서비스에 적용하지 않아야 한다."""

    sheet_name = "101 출발-도착"
    workbook = {
        "405001.xlsx": (
            WorkbookCellRow(sheet_name, 5, (None, "(시행일 : 2026.9.1.)")),
            WorkbookCellRow(sheet_name, 7, (None, "구분", "출발", "도착")),
            WorkbookCellRow(sheet_name, 8, (None, 1, "09:00", "09:40")),
        )
    }

    result = map_exact_weekday_trips(
        workbook,
        (_stop("pattern-a", 1, "출발"), _stop("pattern-a", 2, "도착")),
        target_date=date(2026, 8, 18),
    )

    assert result.mappings == ()
    assert result.issues[0].reason_code == "TIMETABLE_TARGET_DATE_BEFORE_EFFECTIVE_FROM"


def test_exact_mapping_manifest_contains_only_selected_patterns_and_source_lineage() -> None:
    """raw manifest는 exact로 선택된 pattern과 workbook 행 계보만 typed 입력으로 내보내야 한다."""

    workbook = _workbook((None, 1, "09:00", "09:20", "09:40"))
    route_stops = (
        _stop("pattern-a", 1, "출발정류장"),
        _stop("pattern-a", 2, "분기A"),
        _stop("pattern-a", 3, "도착정류장"),
        _stop("pattern-b", 1, "출발정류장"),
        _stop("pattern-b", 2, "분기B"),
        _stop("pattern-b", 3, "도착정류장"),
    )
    result = map_exact_weekday_trips(
        workbook,
        route_stops,
        target_date=date(2026, 8, 18),
    )

    manifest = build_exact_mapping_manifest(
        workbook,
        route_stops,
        result,
        official_snapshot_id="fixture-snapshot",
    )

    assert manifest["official_snapshot_id"] == "fixture-snapshot"
    assert {
        row["provider_route_pattern_id"] for row in manifest["route_stops"]  # type: ignore[index]
    } == {"pattern-a"}
    trip = manifest["trip_rows"][0]  # type: ignore[index]
    assert trip["workbook"] == "405001.xlsx"
    assert trip["sheet_name"] == "101 출발-도착"
    assert trip["source_row_number"] == 8
    assert trip["timetable_effective_from"] == "2024-08-01"


def test_mapping_overlay_preserves_base_and_rewrites_verified_workbook_alias() -> None:
    """동부 overlay는 원본 manifest를 바꾸지 않고 pinned 전역 workbook 이름으로 병합해야 한다."""

    base: dict[str, object] = {
        "workbooks": {"405009.xlsx": {}},
        "route_stops": [],
        "trip_rows": [],
    }
    overlay: dict[str, object] = {
        "route_stops": [
            {
                "provider_route_pattern_id": "east-pattern",
                "provider_stop_id": "east-stop",
                "route_sequence": 1,
            }
        ],
        "trip_rows": [
            {
                "trip_id": "east-trip",
                "workbook": "405009-route-201.xlsx",
            }
        ],
    }

    merged = merge_mapping_manifest_overlay(
        base,
        overlay,
        workbook_aliases={"405009-route-201.xlsx": "405009.xlsx"},
    )

    assert base["route_stops"] == []
    assert base["trip_rows"] == []
    assert merged["trip_rows"][0]["workbook"] == "405009.xlsx"  # type: ignore[index]
    assert merged["route_stops"] == overlay["route_stops"]


def test_incremental_manifest_preserves_old_trip_ids_and_adds_only_new_lineage() -> None:
    """증분 시간표는 기존 source row의 trip ID를 보존하고 새 row에만 안정 ID를 부여해야 한다."""

    base: dict[str, object] = {
        "workbooks": {"405001.xlsx": {}},
        "route_stops": [
            {"provider_route_pattern_id": "pattern-a", "route_sequence": 1}
        ],
        "trip_rows": [
            {
                "trip_id": "old-trip-id",
                "workbook": "405001.xlsx",
                "sheet_name": "101 평일",
                "source_row_number": 8,
                "route_number": "101",
                "provider_route_pattern_ids": ["pattern-a"],
                "major_stop_times": [],
            }
        ],
    }
    expanded: dict[str, object] = {
        "workbooks": {"405001.xlsx": {}},
        "route_stops": [
            {"provider_route_pattern_id": "pattern-a", "route_sequence": 1},
            {"provider_route_pattern_id": "pattern-b", "route_sequence": 1},
        ],
        "trip_rows": [
            {
                **base["trip_rows"][0],  # type: ignore[index]
                "trip_id": "renumbered-trip-id",
            },
            {
                "trip_id": "temporary-sequence-id",
                "workbook": "405001.xlsx",
                "sheet_name": "101 평일",
                "source_row_number": 9,
                "route_number": "101",
                "provider_route_pattern_ids": ["pattern-b"],
                "major_stop_times": [],
            },
        ],
    }

    merged = merge_incremental_mapping_manifest(base, expanded)
    trips = merged["trip_rows"]

    assert trips[0]["trip_id"] == "old-trip-id"  # type: ignore[index]
    assert trips[1]["trip_id"].startswith("weekday-expansion-")  # type: ignore[index]
    assert trips[1]["trip_id"] != "temporary-sequence-id"  # type: ignore[index]


def test_incremental_manifest_rejects_changed_existing_trip_evidence() -> None:
    """기존 source row의 pattern이나 stop-time이 바뀌면 증분 발행을 회귀로 거부해야 한다."""

    base: dict[str, object] = {
        "workbooks": {"405001.xlsx": {}},
        "route_stops": [],
        "trip_rows": [
            {
                "trip_id": "old-trip-id",
                "workbook": "405001.xlsx",
                "sheet_name": "101 평일",
                "source_row_number": 8,
                "route_number": "101",
                "provider_route_pattern_ids": ["pattern-a"],
            }
        ],
    }
    expanded = deepcopy(base)
    expanded["trip_rows"][0]["provider_route_pattern_ids"] = ["pattern-b"]  # type: ignore[index]

    with pytest.raises(
        TimetableWorkbookRejected, match="TIMETABLE_INCREMENTAL_TRIP_REGRESSION"
    ):
        merge_incremental_mapping_manifest(base, expanded)
