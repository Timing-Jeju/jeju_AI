#!/usr/bin/env python3
"""staged TAGO route-stop과 공식 XLSX로 owner-only 평일 시간표 raw ZIP을 만든다."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import cast

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jeju_trip.application.manual_import import validate_timetable_bundle  # noqa: E402
from jeju_trip.infrastructure.official_timetable_bundle import (  # noqa: E402
    build_weekday_timetable_bundle,
)
from jeju_trip.infrastructure.official_timetable_manifest import (  # noqa: E402
    TimetableRouteStop,
    build_mapping_manifest,
)
from jeju_trip.infrastructure.timetable_xlsx import (  # noqa: E402
    load_official_timetable_zip,
    read_official_workbook,
)

ROUTE_STOP_PUBLICATION_ID = "c516eb03-186c-4de4-9788-6c1b4d13b2e9"
WORKBOOKS = {
    "405009-route-201.xlsx": ("201",),
    "405011-route-211-212.xlsx": ("211", "212"),
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--notices-reviewed-through",
        type=date.fromisoformat,
        required=True,
    )
    parser.add_argument(
        "--holiday-source-reference",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--expected-sha256",
        action="append",
        required=True,
        help="filename=sha256 형식이며 모든 workbook을 정확히 한 번 지정한다.",
    )
    parser.add_argument(
        "--route-stop-publication",
        default=ROUTE_STOP_PUBLICATION_ID,
    )
    return parser.parse_args()


def _expected_checksums(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        filename, separator, checksum = value.partition("=")
        if (
            not separator
            or filename not in WORKBOOKS
            or filename in result
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
        ):
            raise SystemExit("TIMETABLE_EXPECTED_CHECKSUM_INVALID")
        result[filename] = checksum
    if set(result) != set(WORKBOOKS):
        raise SystemExit("TIMETABLE_EXPECTED_CHECKSUM_INCOMPLETE")
    return result


def _route_stops(dsn: str, publication_id: str) -> tuple[TimetableRouteStop, ...]:
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            """SELECT replace(route.route_fact_id, 'tago.bus-route:', ''),
                      route_fact.route_number,
                      replace(route.stop_fact_id, 'tago.bus-stop:', ''),
                      route.route_sequence,
                      route.direction_text,
                      ST_Y(route.position::geometry),
                      ST_X(route.position::geometry),
                      stop_fact.name
                 FROM travel_projection.bus_route_stop AS route
                 JOIN travel_projection.bus_stop_fact AS stop_fact
                   ON stop_fact.fact_id = route.stop_fact_id
                 JOIN source_admin.active_snapshot AS active_stop
                   ON active_stop.source_id = 'tago.bus-stop'
                  AND active_stop.publication_id = stop_fact.publication_id
                 JOIN travel_projection.bus_route_fact AS route_fact
                   ON route_fact.fact_id = route.route_fact_id
                 JOIN source_admin.active_snapshot AS active_route
                   ON active_route.source_id = 'tago.bus-route'
                  AND active_route.publication_id = route_fact.publication_id
                WHERE route.publication_id = %s
                  AND route_fact.route_number IN ('201', '211', '212')
                ORDER BY route.route_fact_id, route.route_sequence""",
            (publication_id,),
        ).fetchall()
    return tuple(
        TimetableRouteStop(
            provider_route_pattern_id=str(row[0]),
            route_number=str(row[1]),
            provider_stop_id=str(row[2]),
            route_sequence=int(row[3]),
            direction_text=str(row[4]),
            latitude=float(row[5]),
            longitude=float(row[6]),
            stop_name=str(row[7]),
        )
        for row in rows
    )


def _json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_member(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    member = zipfile.ZipInfo(name, date_time=(2026, 8, 11, 0, 0, 0))
    member.compress_type = zipfile.ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    archive.writestr(member, payload)


def main() -> int:
    arguments = _arguments()
    dsn = os.getenv("JEJU_IMPORTER_DSN")
    if not dsn:
        raise SystemExit("JEJU_IMPORTER_DSN_MISSING")
    expected_checksums = _expected_checksums(arguments.expected_sha256)
    workbook_payloads: dict[str, bytes] = {}
    workbook_rows = {}
    for filename, tokens in WORKBOOKS.items():
        path = arguments.input_dir / filename
        payload = path.read_bytes()
        workbook_payloads[filename] = payload
        with path.open("rb") as stream:
            workbook_rows[filename] = read_official_workbook(
                stream,
                expected_sha256=expected_checksums[filename],
                required_sheet_tokens=tokens,
            )
    mapping_manifest = build_mapping_manifest(
        workbook_rows,
        _route_stops(dsn, arguments.route_stop_publication),
        target_date=arguments.target_date,
    )
    checksum_manifest = {
        "algorithm": "SHA256",
        "files": {
            filename: hashlib.sha256(payload).hexdigest()
            for filename, payload in sorted(workbook_payloads.items())
        },
    }
    service_day_manifest = {
        "weekday_source_references": [
            "https://www.data.go.kr/data/3043887/fileData.do",
            "https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule",
        ],
        "holiday_source_references": [],
        "notice_source_references": ["https://bus.jeju.go.kr/notice/list"],
        "notices_reviewed_through": arguments.notices_reviewed_through.isoformat(),
        "service_exceptions": [],
        "notices": [],
        "route_exceptions": [],
    }
    service_day_manifest["holiday_source_references"] = list(
        dict.fromkeys(arguments.holiday_source_reference)
    )
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for filename, payload in sorted(workbook_payloads.items()):
            _write_member(archive, filename, payload)
        _write_member(archive, "checksum-manifest.json", _json(checksum_manifest))
        _write_member(archive, "mapping-manifest.json", _json(mapping_manifest))
        _write_member(archive, "service-day-manifest.json", _json(service_day_manifest))
    payload = raw.getvalue()
    official = load_official_timetable_zip(io.BytesIO(payload))
    validate_timetable_bundle(build_weekday_timetable_bundle(official, arguments.target_date))
    arguments.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        arguments.output,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
    print(
        json.dumps(
            {
                "status": "Pass",
                "route_stop_rows": len(cast(list[object], mapping_manifest["route_stops"])),
                "trip_rows": len(cast(list[object], mapping_manifest["trip_rows"])),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
