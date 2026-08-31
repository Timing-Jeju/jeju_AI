"""공식 시간표 workbook과 활성 TAGO 노선 카탈로그의 전역 범위를 비교한다."""

from __future__ import annotations

import re
import warnings
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from jeju_trip.infrastructure.timetable_xlsx import (
    TimetableWorkbookRejected,
    WorkbookCellRow,
    read_official_workbook,
)

_ROUTE_NUMBER_PREFIX = re.compile(r"^\s*(\d+(?:-\d+)?)\b")


def read_workbook_checksums(path: Path, input_dir: Path) -> dict[str, str]:
    """workspace-relative SHA256SUMS와 owner-only workbook 집합을 정확히 대조한다."""

    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        checksum, separator, raw_name = line.partition("  ")
        filename = Path(raw_name).name
        if (
            not separator
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
            or not filename.endswith(".xlsx")
            or filename in result
        ):
            raise TimetableWorkbookRejected("TIMETABLE_CHECKSUM_MANIFEST_INVALID")
        result[filename] = checksum
    discovered = {workbook.name for workbook in input_dir.glob("*.xlsx")}
    if not result or set(result) != discovered:
        raise TimetableWorkbookRejected("TIMETABLE_CHECKSUM_MANIFEST_INCOMPLETE")
    return result


def load_verified_workbook_rows(
    input_dir: Path,
    checksums: Mapping[str, str],
) -> dict[str, tuple[WorkbookCellRow, ...]]:
    """checksum·XLSX 안전 검증을 통과한 workbook 값만 메모리에 읽는다."""

    result: dict[str, tuple[WorkbookCellRow, ...]] = {}
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Workbook contains no default style, apply openpyxl's default",
            category=UserWarning,
        )
        for filename, checksum in sorted(checksums.items()):
            with (input_dir / filename).open("rb") as stream:
                result[filename] = read_official_workbook(
                    stream,
                    expected_sha256=checksum,
                    required_sheet_tokens=(),
                )
    return result


def route_number_from_sheet_name(sheet_name: str) -> str:
    """sheet 제목 선두의 공식 노선번호를 하이픈 suffix까지 손실 없이 읽는다."""

    matched = _ROUTE_NUMBER_PREFIX.match(sheet_name)
    if matched is None:
        raise TimetableWorkbookRejected("TIMETABLE_SHEET_ROUTE_NUMBER_MISSING")
    return matched.group(1)


def workbook_route_numbers(
    workbook_rows: Mapping[str, tuple[WorkbookCellRow, ...]],
) -> tuple[str, ...]:
    """검증된 workbook 행에서 중복 없는 공식 노선번호 집합을 만든다."""

    sheet_names = {
        (filename, row.sheet_name)
        for filename, rows in workbook_rows.items()
        for row in rows
    }
    return tuple(sorted({route_number_from_sheet_name(name) for _, name in sheet_names}))


@dataclass(frozen=True)
class TimetableScopeAudit:
    """전역 exact mapping 전 단계의 노선번호·pattern 범위 감사 결과."""

    workbook_count: int
    sheet_count: int
    workbook_route_number_count: int
    active_route_number_count: int
    matched_route_number_count: int
    active_route_pattern_count: int
    matched_route_pattern_count: int
    route_number_coverage: float
    route_pattern_candidate_coverage: float
    workbook_only_route_numbers: tuple[str, ...]
    catalog_only_route_numbers: tuple[str, ...]
    scope_catalog_aligned: bool
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """감사 결과를 정렬 가능한 JSON 값으로 변환한다."""

        return asdict(self)


def audit_timetable_scope(
    workbook_rows: Mapping[str, tuple[WorkbookCellRow, ...]],
    active_route_pattern_counts: Mapping[str, int],
) -> TimetableScopeAudit:
    """공식 workbook과 활성 TAGO 노선번호의 양방향 누락을 fail-closed로 계산한다."""

    if not workbook_rows:
        raise TimetableWorkbookRejected("TIMETABLE_WORKBOOKS_EMPTY")
    if not active_route_pattern_counts or any(
        not route_number or count <= 0
        for route_number, count in active_route_pattern_counts.items()
    ):
        raise TimetableWorkbookRejected("TIMETABLE_ACTIVE_ROUTE_CATALOG_EMPTY")

    workbook_numbers = set(workbook_route_numbers(workbook_rows))
    catalog_numbers = set(active_route_pattern_counts)
    matched = workbook_numbers & catalog_numbers
    workbook_only = tuple(sorted(workbook_numbers - catalog_numbers))
    catalog_only = tuple(sorted(catalog_numbers - workbook_numbers))
    active_patterns = sum(active_route_pattern_counts.values())
    matched_patterns = sum(active_route_pattern_counts[number] for number in matched)
    blocking_reasons: list[str] = []
    if workbook_only:
        blocking_reasons.append("TIMETABLE_ROUTE_NUMBER_MISSING_FROM_TAGO_CATALOG")
    if catalog_only:
        blocking_reasons.append("TIMETABLE_ROUTE_NUMBER_MISSING_FROM_WORKBOOKS")

    sheets = {
        (filename, row.sheet_name)
        for filename, rows in workbook_rows.items()
        for row in rows
    }
    return TimetableScopeAudit(
        workbook_count=len(workbook_rows),
        sheet_count=len(sheets),
        workbook_route_number_count=len(workbook_numbers),
        active_route_number_count=len(catalog_numbers),
        matched_route_number_count=len(matched),
        active_route_pattern_count=active_patterns,
        matched_route_pattern_count=matched_patterns,
        route_number_coverage=len(matched) / len(catalog_numbers),
        route_pattern_candidate_coverage=matched_patterns / active_patterns,
        workbook_only_route_numbers=workbook_only,
        catalog_only_route_numbers=catalog_only,
        scope_catalog_aligned=not blocking_reasons,
        blocking_reasons=tuple(blocking_reasons),
    )
