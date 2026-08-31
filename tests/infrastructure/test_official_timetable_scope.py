"""공식 시간표와 활성 TAGO 노선 범위의 fail-closed 감사 테스트."""

from __future__ import annotations

import pytest

from jeju_trip.infrastructure.official_timetable_scope import (
    audit_timetable_scope,
    route_number_from_sheet_name,
)
from jeju_trip.infrastructure.timetable_xlsx import (
    TimetableWorkbookRejected,
    WorkbookCellRow,
)


def _rows(*sheet_names: str) -> tuple[WorkbookCellRow, ...]:
    return tuple(WorkbookCellRow(name, 7, (None, "구분")) for name in sheet_names)


def test_sheet_route_number_preserves_hyphen_suffix() -> None:
    """마을·지선 노선번호의 하이픈 suffix를 범위 표현으로 오해하지 않아야 한다."""

    assert route_number_from_sheet_name("102-1 제주터미널-한림고") == "102-1"
    assert route_number_from_sheet_name("451-1 순환(도두동-도두동)") == "451-1"


def test_sheet_without_leading_route_number_is_rejected() -> None:
    """제목에서 공식 노선번호를 확인할 수 없는 sheet는 추정하지 않아야 한다."""

    with pytest.raises(
        TimetableWorkbookRejected,
        match="TIMETABLE_SHEET_ROUTE_NUMBER_MISSING",
    ):
        route_number_from_sheet_name("제주터미널-성산")


def test_scope_audit_reports_both_catalog_gaps_without_global_alignment() -> None:
    """workbook·TAGO 어느 한쪽의 누락도 전역 범위 일치로 판정하지 않아야 한다."""

    audit = audit_timetable_scope(
        {
            "405001.xlsx": _rows("101 남원-공항", "101 공항-남원"),
            "405002.xlsx": _rows("102-1 제주터미널-한림고"),
        },
        {"101": 7, "202-3": 1},
    )

    assert audit.workbook_count == 2
    assert audit.sheet_count == 3
    assert audit.matched_route_number_count == 1
    assert audit.matched_route_pattern_count == 7
    assert audit.workbook_only_route_numbers == ("102-1",)
    assert audit.catalog_only_route_numbers == ("202-3",)
    assert audit.scope_catalog_aligned is False
    assert audit.blocking_reasons == (
        "TIMETABLE_ROUTE_NUMBER_MISSING_FROM_TAGO_CATALOG",
        "TIMETABLE_ROUTE_NUMBER_MISSING_FROM_WORKBOOKS",
    )


def test_scope_audit_allows_alignment_only_for_equal_nonempty_route_sets() -> None:
    """양쪽의 비어 있지 않은 노선번호 집합이 같을 때만 범위 정렬을 통과해야 한다."""

    audit = audit_timetable_scope(
        {"405001.xlsx": _rows("101 남원-공항", "101 공항-남원")},
        {"101": 7},
    )

    assert audit.scope_catalog_aligned is True
    assert audit.route_number_coverage == 1
    assert audit.route_pattern_candidate_coverage == 1
    assert audit.blocking_reasons == ()
