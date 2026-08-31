"""공식 workbook 운행행을 활성 TAGO route pattern에 fail-closed로 결합한다."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import cast

from jeju_trip.infrastructure.official_timetable_scope import route_number_from_sheet_name
from jeju_trip.infrastructure.timetable_xlsx import (
    TimetableWorkbookRejected,
    WorkbookCellRow,
)

_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})")
_ALL_CLOCKS = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_PARENTHETICAL = re.compile(r"[\(\[（【]([^\)\]）】]*)[\)\]）】]")
_NON_NAME = re.compile(r"[^0-9A-Za-z가-힣]")
_ROUTE_NUMBER = re.compile(r"\d+(?:-\d+)?")
_ROUTE_TITLE_PREFIX = re.compile(r"^\s*\d+(?:-\d+)?\s*")
_ROUTE_TITLE_SEPARATOR = re.compile(r"[-→↔~>]+")
_EFFECTIVE_DATE = re.compile(
    r"시행일\s*[:：]?\s*'?((?:20)?\d{2})\s*[.]\s*(\d{1,2})\s*[.]\s*(\d{1,2})"
)
_NAME_REPLACEMENTS = (
    ("법환우제국", "법환우체국"),
    ("이호태우", "이호테우"),
    ("제주대학병원", "제주대학교병원"),
    ("제주터미널", "제주버스터미널"),
    ("서귀터미널", "서귀포버스터미널"),
    ("서귀포터미널", "서귀포버스터미널"),
    ("성산포항", "성산항"),
    ("강정항", "강정크루즈여객터미널"),
    ("초교", "초등학교"),
    ("고교", "고등학교"),
    ("제대", "제주대학교"),
    ("신제주R", "신제주로터리"),
    ("중앙R", "중앙로터리"),
    ("동문R", "동문로터리"),
    ("노형R", "노형로터리"),
    ("제원A", "제원아파트"),
    ("서해A", "서해아파트"),
    ("수선화A", "수선화아파트"),
    ("대림A", "대림아파트"),
)
_IGNORED_NAME_PARTS = ("경유", "방면", "가상정류소", "하차전용", "종점")
_IGNORED_ROUTE_TITLE_PARTS = (
    "평일",
    "휴일",
    "토요일",
    "시간표",
    "변경후",
    "임시운행",
    "심야",
    "순환",
)


@dataclass(frozen=True)
class TimetableRouteStop:
    """exact mapping에 필요한 활성 TAGO route-stop의 최소 필드."""

    provider_route_pattern_id: str
    route_number: str
    provider_stop_id: str
    route_sequence: int
    direction_text: str
    latitude: float
    longitude: float
    stop_name: str


@dataclass(frozen=True)
class ExactStopTime:
    """workbook 시각이 유일한 TAGO stop에 결합된 값."""

    provider_stop_id: str
    route_sequence: int
    stop_sequence: int
    arrival_time: str
    departure_time: str


@dataclass(frozen=True)
class ExactTripMapping:
    """하나의 공식 운행행과 유일한 TAGO route pattern의 결합."""

    workbook: str
    sheet_name: str
    source_row_number: int
    route_number: str
    provider_route_pattern_id: str
    direction_text: str
    timetable_effective_from: date
    timetable_effective_to: date
    stop_times: tuple[ExactStopTime, ...]


@dataclass(frozen=True)
class TimetableMappingIssue:
    """추정 없이 제외한 workbook 행의 구조화된 사유."""

    reason_code: str
    workbook: str
    sheet_name: str
    source_row_number: int | None = None
    route_number: str | None = None


@dataclass(frozen=True)
class ExactTimetableMappingResult:
    """exact mapping과 제외 사유를 함께 보존하는 감사 결과."""

    mappings: tuple[ExactTripMapping, ...]
    issues: tuple[TimetableMappingIssue, ...]

    def summary(self) -> dict[str, object]:
        """원본 내용을 노출하지 않는 집계 JSON을 만든다."""

        reasons = Counter(issue.reason_code for issue in self.issues)
        excluded_nonweekday = reasons.get("TIMETABLE_NON_WEEKDAY_ROW_EXCLUDED", 0)
        blocking_reasons = {
            reason: count
            for reason, count in reasons.items()
            if reason != "TIMETABLE_NON_WEEKDAY_ROW_EXCLUDED"
        }
        weekday_candidate_rows = len(self.mappings) + sum(blocking_reasons.values())
        return {
            "exact_trip_rows": len(self.mappings),
            "weekday_candidate_rows": weekday_candidate_rows,
            "exact_trip_row_ratio": len(self.mappings) / weekday_candidate_rows
            if weekday_candidate_rows
            else 0.0,
            "exact_route_numbers": len({item.route_number for item in self.mappings}),
            "exact_route_patterns": len(
                {item.provider_route_pattern_id for item in self.mappings}
            ),
            "excluded_nonweekday_rows": excluded_nonweekday,
            "issue_counts": dict(sorted(reasons.items())),
            "blocking_issue_counts": dict(sorted(blocking_reasons.items())),
            "all_weekday_rows_exact": not blocking_reasons,
        }

    def mapping_rows(self) -> list[dict[str, object]]:
        """후속 manifest builder가 사용할 JSON-safe exact 행만 반환한다."""

        return [asdict(item) for item in self.mappings]


def build_exact_mapping_manifest(
    workbook_rows: Mapping[str, tuple[WorkbookCellRow, ...]],
    route_stops: tuple[TimetableRouteStop, ...],
    result: ExactTimetableMappingResult,
    *,
    official_snapshot_id: str,
) -> dict[str, object]:
    """exact mapping만 포함한 전역 raw ZIP용 lineage manifest를 만든다."""

    if not official_snapshot_id.strip() or not result.mappings:
        raise TimetableWorkbookRejected("TIMETABLE_EXACT_MAPPING_EMPTY")
    selected_patterns = {item.provider_route_pattern_id for item in result.mappings}
    selected_route_stops = tuple(
        item for item in route_stops if item.provider_route_pattern_id in selected_patterns
    )
    if {item.provider_route_pattern_id for item in selected_route_stops} != selected_patterns:
        raise TimetableWorkbookRejected("TIMETABLE_EXACT_ROUTE_STOPS_INCOMPLETE")

    workbooks: dict[str, dict[str, object]] = {}
    for filename, rows in sorted(workbook_rows.items()):
        sheet_names = {row.sheet_name for row in rows}
        required_tokens = sorted(
            {route_number_from_sheet_name(sheet_name) for sheet_name in sheet_names}
        )
        workbooks[filename] = {
            "route_group": Path(filename).stem,
            "required_sheet_tokens": required_tokens,
        }

    trip_rows: list[dict[str, object]] = []
    for sequence, mapping in enumerate(result.mappings, start=1):
        trip_rows.append(
            {
                "trip_id": (
                    f"weekday-{mapping.timetable_effective_to:%Y%m%d}-"
                    f"{mapping.route_number}-{sequence:05d}"
                ),
                "route_number": mapping.route_number,
                "provider_route_pattern_ids": [mapping.provider_route_pattern_id],
                "direction_text": mapping.direction_text,
                "timetable_effective_from": mapping.timetable_effective_from.isoformat(),
                "timetable_effective_to": mapping.timetable_effective_to.isoformat(),
                "workbook": mapping.workbook,
                "sheet_name": mapping.sheet_name,
                "source_row_number": mapping.source_row_number,
                "major_stop_times": [
                    {
                        "provider_stop_id": item.provider_stop_id,
                        "route_sequence": item.route_sequence,
                        "stop_sequence": item.stop_sequence,
                        "arrival_time": item.arrival_time,
                        "departure_time": item.departure_time,
                    }
                    for item in mapping.stop_times
                ],
            }
        )
    return {
        "official_snapshot_id": official_snapshot_id,
        "workbooks": workbooks,
        "route_stops": [
            {
                "provider_route_pattern_id": item.provider_route_pattern_id,
                "provider_stop_id": item.provider_stop_id,
                "route_sequence": item.route_sequence,
                "direction_text": item.direction_text,
                "latitude": item.latitude,
                "longitude": item.longitude,
            }
            for item in sorted(
                selected_route_stops,
                key=lambda item: (item.provider_route_pattern_id, item.route_sequence),
            )
        ],
        "trip_rows": trip_rows,
    }


def merge_mapping_manifest_overlay(
    base: dict[str, object],
    overlay: dict[str, object],
    *,
    workbook_aliases: Mapping[str, str],
) -> dict[str, object]:
    """기존 exact manifest에 검증된 별도 mapping을 충돌 없이 병합한다."""

    merged = deepcopy(base)
    workbooks = merged.get("workbooks")
    base_route_stops = merged.get("route_stops")
    base_trip_rows = merged.get("trip_rows")
    overlay_route_stops = overlay.get("route_stops")
    overlay_trip_rows = overlay.get("trip_rows")
    if (
        not isinstance(workbooks, dict)
        or not isinstance(base_route_stops, list)
        or not isinstance(base_trip_rows, list)
        or not isinstance(overlay_route_stops, list)
        or not isinstance(overlay_trip_rows, list)
    ):
        raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_INVALID")

    route_keys: dict[tuple[object, object], dict[str, object]] = {}
    for raw_row in (*base_route_stops, *overlay_route_stops):
        if not isinstance(raw_row, dict):
            raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_INVALID")
        row = cast(dict[str, object], raw_row)
        pattern = row.get("provider_route_pattern_id")
        route_sequence = row.get("route_sequence")
        if (
            not isinstance(pattern, str)
            or not isinstance(route_sequence, int)
            or route_sequence <= 0
        ):
            raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_INVALID")
        key = (pattern, route_sequence)
        previous = route_keys.get(key)
        if previous is not None and previous != row:
            raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_ROUTE_CONFLICT")
        route_keys[key] = deepcopy(row)

    trip_ids = {
        row.get("trip_id")
        for row in base_trip_rows
        if isinstance(row, dict)
    }
    for raw_row in overlay_trip_rows:
        if not isinstance(raw_row, dict):
            raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_INVALID")
        row = deepcopy(cast(dict[str, object], raw_row))
        workbook = row.get("workbook")
        if not isinstance(workbook, str) or workbook not in workbook_aliases:
            raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_WORKBOOK_UNKNOWN")
        row["workbook"] = workbook_aliases[workbook]
        if row["workbook"] not in workbooks:
            raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_WORKBOOK_MISSING")
        trip_id = row.get("trip_id")
        if not isinstance(trip_id, str) or trip_id in trip_ids:
            raise TimetableWorkbookRejected("TIMETABLE_MAPPING_OVERLAY_TRIP_CONFLICT")
        trip_ids.add(trip_id)
        base_trip_rows.append(row)

    merged["route_stops"] = sorted(
        route_keys.values(),
        key=lambda row: (
            str(row.get("provider_route_pattern_id", "")),
            int(cast(int, row.get("route_sequence", 0))),
        ),
    )
    return merged


def merge_incremental_mapping_manifest(
    base: dict[str, object],
    expanded: dict[str, object],
) -> dict[str, object]:
    """기존 trip ID를 보존하고 새 공식 workbook 행만 안정 ID로 추가한다."""

    base_workbooks = base.get("workbooks")
    expanded_workbooks = expanded.get("workbooks")
    base_route_stops = base.get("route_stops")
    expanded_route_stops = expanded.get("route_stops")
    base_trip_rows = base.get("trip_rows")
    expanded_trip_rows = expanded.get("trip_rows")
    if (
        not isinstance(base_workbooks, dict)
        or base_workbooks != expanded_workbooks
        or not isinstance(base_route_stops, list)
        or not isinstance(expanded_route_stops, list)
        or not isinstance(base_trip_rows, list)
        or not isinstance(expanded_trip_rows, list)
    ):
        raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_MANIFEST_INVALID")

    expanded_route_keys = {
        (row.get("provider_route_pattern_id"), row.get("route_sequence")): row
        for row in expanded_route_stops
        if isinstance(row, dict)
    }
    if len(expanded_route_keys) != len(expanded_route_stops):
        raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_ROUTE_DUPLICATED")
    for raw_row in base_route_stops:
        if not isinstance(raw_row, dict):
            raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_MANIFEST_INVALID")
        key = (raw_row.get("provider_route_pattern_id"), raw_row.get("route_sequence"))
        if expanded_route_keys.get(key) != raw_row:
            raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_ROUTE_REGRESSION")

    def lineage(row: dict[str, object]) -> tuple[object, ...]:
        return (
            row.get("workbook"),
            row.get("sheet_name"),
            row.get("source_row_number"),
            row.get("route_number"),
        )

    def without_trip_id(row: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in row.items() if key != "trip_id"}

    base_by_lineage: dict[tuple[object, ...], dict[str, object]] = {}
    trip_ids: set[str] = set()
    preserved: list[dict[str, object]] = []
    for raw_row in base_trip_rows:
        if not isinstance(raw_row, dict) or not isinstance(raw_row.get("trip_id"), str):
            raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_MANIFEST_INVALID")
        row = cast(dict[str, object], raw_row)
        key = lineage(row)
        trip_id = cast(str, row["trip_id"])
        if key in base_by_lineage or trip_id in trip_ids:
            raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_BASE_DUPLICATED")
        base_by_lineage[key] = row
        trip_ids.add(trip_id)
        preserved.append(deepcopy(row))

    additions: list[dict[str, object]] = []
    expanded_lineages: set[tuple[object, ...]] = set()
    for raw_row in expanded_trip_rows:
        if not isinstance(raw_row, dict):
            raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_MANIFEST_INVALID")
        row = cast(dict[str, object], raw_row)
        key = lineage(row)
        if key in expanded_lineages:
            raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_EXPANSION_DUPLICATED")
        expanded_lineages.add(key)
        previous = base_by_lineage.get(key)
        if previous is not None:
            if without_trip_id(previous) != without_trip_id(row):
                raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_TRIP_REGRESSION")
            continue
        stable_key = json.dumps(key, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        addition = deepcopy(row)
        addition["trip_id"] = (
            "weekday-expansion-"
            + hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
        )
        if addition["trip_id"] in trip_ids:
            raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_TRIP_ID_CONFLICT")
        trip_ids.add(cast(str, addition["trip_id"]))
        additions.append(addition)

    if set(base_by_lineage) - expanded_lineages:
        raise TimetableWorkbookRejected("TIMETABLE_INCREMENTAL_TRIP_REMOVED")
    merged = deepcopy(expanded)
    merged["trip_rows"] = preserved + sorted(additions, key=lineage)
    return merged


@lru_cache(maxsize=16_384)
def _cached_name_variants(text: str) -> tuple[str, ...]:
    raw = unicodedata.normalize("NFKC", text).replace("\n", "")
    values = [raw, _PARENTHETICAL.sub("", raw)]
    values.extend(_PARENTHETICAL.findall(raw))
    result: list[str] = []
    for candidate in values:
        normalized = candidate
        for source, target in _NAME_REPLACEMENTS:
            normalized = normalized.replace(source, target)
        for ignored in _IGNORED_NAME_PARTS:
            normalized = normalized.replace(ignored, "")
        normalized = _NON_NAME.sub("", normalized).lower()
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _name_variants(value: object) -> tuple[str, ...]:
    return _cached_name_variants(str(value or ""))


def _stop_name_matches(checkpoint: object, stop_name: str) -> bool:
    checkpoint_variants = _name_variants(checkpoint)
    stop_variants = _name_variants(stop_name)
    return any(
        min(len(left), len(right)) >= 2 and (left in right or right in left)
        for left in checkpoint_variants
        for right in stop_variants
    )


def _stop_name_exactly_matches(checkpoint: object, stop_name: str) -> bool:
    """승인된 표기 정규화 뒤 양쪽 이름이 완전히 같은지 판정한다."""

    return bool(set(_name_variants(checkpoint)) & set(_name_variants(stop_name)))


def _clock(value: object) -> tuple[int, int] | None:
    matched = _CLOCK.match(str(value or ""))
    if matched is None:
        return None
    hour = int(matched.group(1))
    minute = int(matched.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _route_number(
    sheet_name: str,
    headers: tuple[object, ...],
    values: tuple[object, ...],
) -> str:
    route_column = next(
        (
            index
            for index, header in enumerate(headers)
            if "노선번호" in "".join(_name_variants(header))
        ),
        None,
    )
    if route_column is not None and route_column < len(values):
        matched = _ROUTE_NUMBER.search(str(values[route_column] or ""))
        if matched is not None:
            return matched.group(0)
    return route_number_from_sheet_name(sheet_name)


def _effective_date(sheet_rows: Mapping[int, tuple[object, ...]]) -> date | None:
    metadata = " ".join(
        str(value or "")
        for row_number, values in sheet_rows.items()
        if row_number < 7
        for value in values
    )
    matched = _EFFECTIVE_DATE.search(metadata)
    if matched is None:
        return None
    year = int(matched.group(1))
    if year < 100:
        year += 2000
    try:
        return date(year, int(matched.group(2)), int(matched.group(3)))
    except ValueError:
        return None


def _candidate_checkpoint_matches(
    route: tuple[TimetableRouteStop, ...],
    headers: tuple[object, ...],
    values: tuple[object, ...],
) -> tuple[tuple[TimetableRouteStop, tuple[int, int], bool], ...]:
    selected: list[tuple[TimetableRouteStop, tuple[int, int], bool]] = []
    last_route_sequence = 0
    for index, value in enumerate(values):
        parsed = _clock(value)
        if parsed is None or index >= len(headers):
            continue
        matches = tuple(
            item
            for item in route
            if item.route_sequence > last_route_sequence
            and _stop_name_matches(headers[index], item.stop_name)
        )
        if len(matches) != 1:
            continue
        selected.append(
            (
                matches[0],
                parsed,
                _stop_name_exactly_matches(headers[index], matches[0].stop_name),
            )
        )
        last_route_sequence = matches[0].route_sequence
    return tuple(selected)


def _candidate_stop_times(
    route: tuple[TimetableRouteStop, ...],
    headers: tuple[object, ...],
    values: tuple[object, ...],
) -> tuple[ExactStopTime, ...]:
    selected = _candidate_checkpoint_matches(route, headers, values)

    result: list[ExactStopTime] = []
    previous_minutes = -1
    day_offset = 0
    for item, (hour, minute), _ in selected:
        minutes = hour * 60 + minute + day_offset * 1440
        if minutes < previous_minutes:
            if previous_minutes % 1440 < 18 * 60 or hour > 6:
                return ()
            day_offset += 1
            minutes += 1440
        service_hour = hour + day_offset * 24
        clock = f"{service_hour:02d}{minute:02d}"
        result.append(
            ExactStopTime(
                provider_stop_id=item.provider_stop_id,
                route_sequence=item.route_sequence,
                stop_sequence=len(result) + 1,
                arrival_time=clock,
                departure_time=clock,
            )
        )
        previous_minutes = minutes
    return tuple(result)


def _candidate_exact_name_match_count(
    route: tuple[TimetableRouteStop, ...],
    headers: tuple[object, ...],
    values: tuple[object, ...],
) -> int:
    """선택된 시각 checkpoint 중 정규화 이름이 완전히 같은 수를 센다."""

    return sum(
        exactly_matches
        for _, _, exactly_matches in _candidate_checkpoint_matches(route, headers, values)
    )


def _route_title_match_count(
    route: tuple[TimetableRouteStop, ...], sheet_name: str
) -> int:
    """공식 sheet 제목의 방향 waypoint가 route-stop 순서와 유일하게 맞는 수를 센다."""

    title = _ROUTE_TITLE_PREFIX.sub("", sheet_name)
    last_route_sequence = 0
    matched = 0
    for raw_token in _ROUTE_TITLE_SEPARATOR.split(title):
        token = raw_token
        for ignored in _IGNORED_ROUTE_TITLE_PARTS:
            token = token.replace(ignored, "")
        matches = tuple(
            item
            for item in route
            if item.route_sequence > last_route_sequence
            and _stop_name_matches(token, item.stop_name)
        )
        if len(matches) != 1:
            continue
        matched += 1
        last_route_sequence = matches[0].route_sequence
    return matched


def _issue(
    reason_code: str,
    workbook: str,
    sheet_name: str,
    row_number: int | None = None,
    route_number: str | None = None,
) -> TimetableMappingIssue:
    return TimetableMappingIssue(
        reason_code=reason_code,
        workbook=workbook,
        sheet_name=sheet_name,
        source_row_number=row_number,
        route_number=route_number,
    )


def map_exact_weekday_trips(
    workbook_rows: Mapping[str, tuple[WorkbookCellRow, ...]],
    route_stops: tuple[TimetableRouteStop, ...],
    *,
    target_date: date,
) -> ExactTimetableMappingResult:
    """평일 운행행 중 route pattern과 정류장 시각이 유일한 행만 결합한다."""

    if target_date.isoweekday() > 5:
        raise TimetableWorkbookRejected("TIMETABLE_TARGET_NOT_WEEKDAY")
    grouped_routes: dict[str, list[TimetableRouteStop]] = defaultdict(list)
    for item in route_stops:
        grouped_routes[item.provider_route_pattern_id].append(item)
    ordered_routes = {
        route_id: tuple(sorted(items, key=lambda item: item.route_sequence))
        for route_id, items in grouped_routes.items()
    }
    routes_by_number: dict[str, list[str]] = defaultdict(list)
    for route_id, items in ordered_routes.items():
        if items:
            routes_by_number[items[0].route_number].append(route_id)

    mappings: list[ExactTripMapping] = []
    issues: list[TimetableMappingIssue] = []
    seen_rows: set[tuple[object, ...]] = set()
    for workbook, rows in sorted(workbook_rows.items()):
        sheets: dict[str, dict[int, tuple[object, ...]]] = defaultdict(dict)
        for row in rows:
            sheets[row.sheet_name][row.row_number] = row.values
        for sheet_name, sheet_rows in sorted(sheets.items()):
            headers = sheet_rows.get(7)
            if headers is None:
                issues.append(_issue("TIMETABLE_HEADER_MISSING", workbook, sheet_name))
                continue
            excluded_day = "휴일" in sheet_name or "토요일" in sheet_name
            effective_from = _effective_date(sheet_rows)
            for row_number, values in sorted(sheet_rows.items()):
                if row_number < 8:
                    continue
                clock_counts = tuple(len(_ALL_CLOCKS.findall(str(value or ""))) for value in values)
                if sum(clock_counts) < 2:
                    continue
                if excluded_day:
                    issues.append(
                        _issue(
                            "TIMETABLE_NON_WEEKDAY_ROW_EXCLUDED",
                            workbook,
                            sheet_name,
                            row_number,
                        )
                    )
                    continue
                if effective_from is None:
                    issues.append(
                        _issue(
                            "TIMETABLE_EFFECTIVE_DATE_MISSING",
                            workbook,
                            sheet_name,
                            row_number,
                        )
                    )
                    continue
                if effective_from > target_date:
                    issues.append(
                        _issue(
                            "TIMETABLE_TARGET_DATE_BEFORE_EFFECTIVE_FROM",
                            workbook,
                            sheet_name,
                            row_number,
                        )
                    )
                    continue
                if any("실시간호출형" in "".join(_name_variants(value)) for value in values):
                    issues.append(
                        _issue(
                            "TIMETABLE_DEMAND_RESPONSIVE_ROW_UNSUPPORTED",
                            workbook,
                            sheet_name,
                            row_number,
                        )
                    )
                    continue
                if any(count > 1 for count in clock_counts):
                    issues.append(
                        _issue(
                            "TIMETABLE_MULTIPLE_CLOCKS_PER_CELL_UNSUPPORTED",
                            workbook,
                            sheet_name,
                            row_number,
                        )
                    )
                    continue
                try:
                    route_number = _route_number(sheet_name, headers, values)
                except TimetableWorkbookRejected:
                    issues.append(
                        _issue(
                            "TIMETABLE_ROW_ROUTE_NUMBER_MISSING",
                            workbook,
                            sheet_name,
                            row_number,
                        )
                    )
                    continue
                identity = (workbook, route_number, headers, tuple(values[1:]))
                if identity in seen_rows:
                    continue
                seen_rows.add(identity)
                candidate_ids = routes_by_number.get(route_number, [])
                if not candidate_ids:
                    issues.append(
                        _issue(
                            "TIMETABLE_ROUTE_NUMBER_MISSING_FROM_TAGO_CATALOG",
                            workbook,
                            sheet_name,
                            row_number,
                            route_number,
                        )
                    )
                    continue
                candidates = [
                    (
                        route_id,
                        _candidate_stop_times(ordered_routes[route_id], headers, values),
                    )
                    for route_id in candidate_ids
                ]
                best_count = max(len(stop_times) for _, stop_times in candidates)
                best = tuple(item for item in candidates if len(item[1]) == best_count)
                if best_count < 2:
                    issues.append(
                        _issue(
                            "TIMETABLE_MAJOR_STOP_TIMES_INSUFFICIENT",
                            workbook,
                            sheet_name,
                            row_number,
                            route_number,
                        )
                    )
                    continue
                if len(best) != 1:
                    full_endpoint = tuple(
                        item
                        for item in best
                        if item[1][0].route_sequence
                        == ordered_routes[item[0]][0].route_sequence
                        and item[1][-1].route_sequence
                        == ordered_routes[item[0]][-1].route_sequence
                    )
                    if len(full_endpoint) == 1:
                        best = full_endpoint
                    else:
                        exact_name_scores = tuple(
                            (
                                _candidate_exact_name_match_count(
                                    ordered_routes[route_id], headers, values
                                ),
                                route_id,
                                stop_times,
                            )
                            for route_id, stop_times in best
                        )
                        best_exact_name_count = max(
                            item[0] for item in exact_name_scores
                        )
                        exact_name_best = tuple(
                            (route_id, stop_times)
                            for count, route_id, stop_times in exact_name_scores
                            if count == best_exact_name_count
                        )
                        if best_exact_name_count > 0 and len(exact_name_best) == 1:
                            best = exact_name_best
                    if len(best) != 1:
                        title_scores = tuple(
                            (
                                _route_title_match_count(
                                    ordered_routes[route_id], sheet_name
                                ),
                                route_id,
                                stop_times,
                            )
                            for route_id, stop_times in best
                        )
                        best_title_count = max(item[0] for item in title_scores)
                        title_best = tuple(
                            (route_id, stop_times)
                            for count, route_id, stop_times in title_scores
                            if count == best_title_count
                        )
                        if best_title_count >= 2 and len(title_best) == 1:
                            best = title_best
                        else:
                            issues.append(
                                _issue(
                                    "TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE",
                                    workbook,
                                    sheet_name,
                                    row_number,
                                    route_number,
                                )
                            )
                            continue
                route_id, stop_times = best[0]
                route = ordered_routes[route_id]
                mappings.append(
                    ExactTripMapping(
                        workbook=workbook,
                        sheet_name=sheet_name,
                        source_row_number=row_number,
                        route_number=route_number,
                        provider_route_pattern_id=route_id,
                        direction_text=route[0].direction_text,
                        timetable_effective_from=effective_from,
                        timetable_effective_to=target_date,
                        stop_times=stop_times,
                    )
                )
    return ExactTimetableMappingResult(tuple(mappings), tuple(issues))
