"""공식 XLSX 운행행과 staged TAGO route-stop을 재현 가능한 manifest로 결합한다."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from jeju_trip.infrastructure.official_timetable_mapping import TimetableRouteStop
from jeju_trip.infrastructure.timetable_xlsx import (
    TimetableWorkbookRejected,
    WorkbookCellRow,
)


def _endpoint(value: str) -> str:
    compact = value.replace("\n", "").replace(" ", "")
    if ("제주버스터미널" in compact or "제주터미널" in compact) and "서귀포" not in compact:
        return "JEJU_TERMINAL"
    if "서귀포버스터미널" in compact or "서귀포터미널" in compact:
        return "SEOGWIPO_TERMINAL"
    if "성산포항" in compact or "성산항" in compact:
        return "SEONGSAN_PORT"
    if "성산일출봉" in compact or compact == "성산":
        return "SEONGSAN_PEAK"
    return compact


def _route_features(names: tuple[str, ...]) -> tuple[bool, ...]:
    joined = "|".join(names)
    return (
        "효돈초등학교" in joined,
        "세화고등학교" in joined,
        "오조리상동입구" in joined,
        "금백조로입구" in joined,
        "수산초등학교" in joined,
        "사려니숲길" in joined,
    )


def _marker(headers: tuple[object, ...], values: tuple[object, ...], token: str) -> bool:
    for index, header in enumerate(headers):
        if token not in str(header or ""):
            continue
        value = str(values[index] or "").strip().upper()
        return value in {"O", "○"}
    return False


def _clock(value: object) -> str | None:
    matched = re.match(r"\s*(\d{1,2}):(\d{2})", str(value or ""))
    if matched is None:
        return None
    return f"{int(matched.group(1)):02d}{matched.group(2)}"


def _checkpoint(header: object, value: object) -> str | None:
    cell = str(value or "")
    if "성산일출봉 출발" in cell or "성산일출봉 종료" in cell:
        return "SEONGSAN_PEAK"
    compact = str(header or "").replace("\n", "").replace(" ", "")
    mappings = (
        (("서귀포버스터미널", "서귀포터미널"), "SEOGWIPO_TERMINAL"),
        (("제주버스터미널", "제주터미널"), "JEJU_TERMINAL"),
        (("성산포항",), "SEONGSAN_PORT"),
        (("성산",), "SEONGSAN_PEAK"),
        (("고성",), "GOSEONG"),
        (("함덕",), "HAMDEOK"),
        (("김녕",), "GIMNYEONG"),
        (("세화",), "SEHWA"),
        (("신산",), "SINSAN"),
        (("표선",), "PYOSEON"),
        (("남원",), "NAMWON"),
        (("서귀포중앙R",), "SEOGWIPO_CENTER"),
        (("수산1리비석거리",), "SUSAN1"),
        (("수산2리입구",), "SUSAN2"),
        (("대천동사거리",), "DAECHEON"),
        (("교래사거리",), "GYORAE"),
        (("제주대학교병원",), "JEJU_HOSPITAL"),
        (("봉개",), "BONGGAE"),
        (("제주시청",), "JEJU_CITY_HALL"),
    )
    for tokens, checkpoint in mappings:
        if any(token in compact for token in tokens):
            return checkpoint
    return None


def _stop_matches(checkpoint: str, name: str) -> bool:
    patterns = {
        "JEJU_TERMINAL": r"^제주버스터미널(?:\(|$)",
        "SEOGWIPO_TERMINAL": r"^서귀포버스터미널(?:\(|$)",
        "SEONGSAN_PORT": r"^성산항(?:\(|$)",
        "SEONGSAN_PEAK": r"^성산일출봉입구(?:\[|$)",
        "GOSEONG": r"^고성환승정류장",
        "HAMDEOK": r"^함덕환승정류장",
        "GIMNYEONG": r"^김녕환승정류장",
        "SEHWA": r"^세화환승정류장",
        "SINSAN": r"^신산환승정류장",
        "PYOSEON": r"^표선환승정류장",
        "NAMWON": r"^남원환승정류장",
        "SEOGWIPO_CENTER": r"^중앙로터리",
        "SUSAN1": r"^수산1리 비석거리",
        "SUSAN2": r"^수산2리 입구$",
        "DAECHEON": r"^대천환승정류장",
        "GYORAE": r"^교래사거리",
        "JEJU_HOSPITAL": r"^제주대학교병원",
        "BONGGAE": r"^봉개동(?:\[|$)",
        "JEJU_CITY_HALL": r"^제주시청",
    }
    pattern = patterns.get(checkpoint)
    return pattern is not None and re.search(pattern, name) is not None


def _route_signature(stops: tuple[TimetableRouteStop, ...]) -> tuple[object, ...]:
    return (
        stops[0].route_number,
        _endpoint(stops[0].stop_name),
        _endpoint(stops[-1].stop_name),
        *_route_features(tuple(item.stop_name for item in stops)),
    )


def _row_signature(
    route_number: str,
    headers: tuple[object, ...],
    values: tuple[object, ...],
    timed_cells: tuple[tuple[int, str], ...],
) -> tuple[object, ...]:
    first_index = timed_cells[0][0]
    last_index = timed_cells[-1][0]
    start = _endpoint(str(headers[first_index] or ""))
    end = _endpoint(str(headers[last_index] or ""))
    if "성산일출봉 출발" in str(values[first_index] or ""):
        start = "SEONGSAN_PEAK"
    if "성산일출봉 종료" in str(values[last_index] or ""):
        end = "SEONGSAN_PEAK"
    return (
        route_number,
        start,
        end,
        _marker(headers, values, "효돈초교"),
        _marker(headers, values, "세화고"),
        _marker(headers, values, "오조"),
        _marker(headers, values, "금백조"),
        _marker(headers, values, "수산초등학교"),
        route_number == "212",
    )


def _major_stop_times(
    route: tuple[TimetableRouteStop, ...],
    headers: tuple[object, ...],
    values: tuple[object, ...],
    timed_cells: tuple[tuple[int, str], ...],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    last_route_sequence = 0
    for index, clock in timed_cells:
        checkpoint = _checkpoint(headers[index], values[index])
        if checkpoint is None:
            continue
        matches = tuple(
            item
            for item in route
            if item.route_sequence > last_route_sequence
            and _stop_matches(checkpoint, item.stop_name)
        )
        if len(matches) != 1:
            # 같은 이름의 물리 정류장이 둘 이상이면 provider stop ID를 추정하지 않는다.
            continue
        selected = matches[0]
        last_route_sequence = selected.route_sequence
        result.append(
            {
                "provider_stop_id": selected.provider_stop_id,
                "route_sequence": selected.route_sequence,
                "stop_sequence": len(result) + 1,
                "arrival_time": clock,
                "departure_time": clock,
            }
        )
    if len(result) < 2:
        raise TimetableWorkbookRejected("TIMETABLE_MAJOR_STOP_TIMES_INSUFFICIENT")
    return result


def build_mapping_manifest(
    workbook_rows: dict[str, tuple[WorkbookCellRow, ...]],
    route_stops: tuple[TimetableRouteStop, ...],
    *,
    target_date: date,
) -> dict[str, object]:
    """XLSX 180개 운행행을 유일 TAGO pattern과 검증 가능한 주요 정류장에 결합한다."""

    grouped: dict[str, list[TimetableRouteStop]] = defaultdict(list)
    for item in route_stops:
        grouped[item.provider_route_pattern_id].append(item)
    ordered_routes = {
        route_id: tuple(sorted(items, key=lambda item: item.route_sequence))
        for route_id, items in grouped.items()
    }
    if len(ordered_routes) != 38 or len(route_stops) != 4239:
        raise TimetableWorkbookRejected("TIMETABLE_ROUTE_SCOPE_MISMATCH")
    signatures: dict[tuple[object, ...], list[str]] = defaultdict(list)
    for route_id, items in ordered_routes.items():
        signatures[_route_signature(items)].append(route_id)

    trip_rows: list[dict[str, object]] = []
    seen_rows: set[tuple[object, ...]] = set()
    for filename, rows in sorted(workbook_rows.items()):
        sheets: dict[str, dict[int, tuple[object, ...]]] = defaultdict(dict)
        for item in rows:
            sheets[item.sheet_name][item.row_number] = item.values
        for sheet_name, sheet_rows in sorted(sheets.items()):
            headers = sheet_rows.get(7)
            if headers is None:
                raise TimetableWorkbookRejected("TIMETABLE_HEADER_MISSING")
            for row_number, values in sorted(sheet_rows.items()):
                if row_number < 8:
                    continue
                identity = tuple(values[1:])
                if identity in seen_rows:
                    continue
                seen_rows.add(identity)
                timed_cells = tuple(
                    (index, parsed)
                    for index, value in enumerate(values)
                    if (parsed := _clock(value)) is not None
                )
                if len(timed_cells) < 2:
                    continue
                route_number = str(int(float(str(values[2])))) if "211-212" in filename else "201"
                signature = _row_signature(route_number, headers, values, timed_cells)
                matches = signatures.get(signature, [])
                if len(matches) != 1:
                    raise TimetableWorkbookRejected("TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE")
                route_id = matches[0]
                route = ordered_routes[route_id]
                effective_from = (
                    date(2026, 2, 12) if route_number in {"211", "212"} else date(2024, 8, 1)
                )
                trip_rows.append(
                    {
                        "trip_id": (
                            f"weekday-{target_date:%Y%m%d}-{route_number}-{len(trip_rows) + 1:03d}"
                        ),
                        "route_number": route_number,
                        "provider_route_pattern_ids": [route_id],
                        "direction_text": route[0].direction_text,
                        "timetable_effective_from": effective_from.isoformat(),
                        "timetable_effective_to": target_date.isoformat(),
                        "workbook": filename,
                        "sheet_name": sheet_name,
                        "source_row_number": row_number,
                        "major_stop_times": _major_stop_times(route, headers, values, timed_cells),
                    }
                )
    if len(trip_rows) != 180:
        raise TimetableWorkbookRejected("TIMETABLE_TRIP_ROW_COUNT_MISMATCH")
    return {
        "workbooks": {
            "405009-route-201.xlsx": {
                "route_group": "201",
                "required_sheet_tokens": ["201"],
            },
            "405011-route-211-212.xlsx": {
                "route_group": "211-212",
                "required_sheet_tokens": ["211", "212"],
            },
        },
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
                route_stops,
                key=lambda item: (item.provider_route_pattern_id, item.route_sequence),
            )
        ],
        "trip_rows": trip_rows,
    }
