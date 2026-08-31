#!/usr/bin/env python3
"""pinned 공식 234개 XLSX와 활성 TAGO로 exact 평일 시간표 raw ZIP을 만든다."""

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
    build_mapping_manifest,
)
from jeju_trip.infrastructure.official_timetable_mapping import (  # noqa: E402
    ExactTimetableMappingResult,
    TimetableRouteStop,
    build_exact_mapping_manifest,
    map_exact_weekday_trips,
    merge_incremental_mapping_manifest,
    merge_mapping_manifest_overlay,
)
from jeju_trip.infrastructure.official_timetable_scope import (  # noqa: E402
    load_verified_workbook_rows,
    read_workbook_checksums,
)
from jeju_trip.infrastructure.timetable_xlsx import (  # noqa: E402
    load_official_timetable_zip,
)

OFFICIAL_SNAPSHOT_ID = "jeju-bis-2026-08-17-234"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--checksum-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-bundle", type=Path)
    parser.add_argument("--expected-base-sha256")
    parser.add_argument("--target-date", type=date.fromisoformat, required=True)
    parser.add_argument("--notices-reviewed-through", type=date.fromisoformat, required=True)
    parser.add_argument("--holiday-source-reference", action="append", default=[])
    parser.add_argument("--expected-exact-trips", type=int, required=True)
    parser.add_argument("--expected-exact-route-numbers", type=int, required=True)
    parser.add_argument("--expected-exact-patterns", type=int, required=True)
    parser.add_argument("--expected-combined-trips", type=int, required=True)
    parser.add_argument("--expected-combined-route-numbers", type=int, required=True)
    parser.add_argument("--expected-combined-patterns", type=int, required=True)
    return parser.parse_args()


def _route_stops(dsn: str) -> tuple[TimetableRouteStop, ...]:
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            """SELECT replace(route_stop.route_fact_id, 'tago.bus-route:', ''),
                      route.route_number,
                      replace(route_stop.stop_fact_id, 'tago.bus-stop:', ''),
                      route_stop.route_sequence,
                      coalesce(route_stop.direction_text, ''),
                      ST_Y(route_stop.position::geometry),
                      ST_X(route_stop.position::geometry),
                      stop.name
                 FROM travel_projection.bus_route_stop AS route_stop
                 JOIN source_admin.active_snapshot AS active_route_stop
                   ON active_route_stop.publication_id = route_stop.publication_id
                  AND active_route_stop.source_id = 'tago.bus-route-stops'
                 JOIN travel_projection.bus_route_fact AS route
                   ON route.fact_id = route_stop.route_fact_id
                 JOIN source_admin.active_snapshot AS active_route
                   ON active_route.publication_id = route.publication_id
                  AND active_route.source_id = 'tago.bus-route'
                 JOIN travel_projection.bus_stop_fact AS stop
                   ON stop.fact_id = route_stop.stop_fact_id
                 JOIN source_admin.active_snapshot AS active_stop
                   ON active_stop.publication_id = stop.publication_id
                  AND active_stop.source_id = 'tago.bus-stop'
                ORDER BY route_stop.route_fact_id, route_stop.route_sequence"""
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


def _write_member(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    target_date: date,
) -> None:
    member = zipfile.ZipInfo(
        name,
        date_time=(target_date.year, target_date.month, target_date.day, 0, 0, 0),
    )
    member.compress_type = zipfile.ZIP_DEFLATED
    member.external_attr = 0o600 << 16
    archive.writestr(member, payload)


def _assert_expected_counts(
    result: ExactTimetableMappingResult,
    arguments: argparse.Namespace,
) -> None:
    summary = result.summary()
    expected = {
        "exact_trip_rows": arguments.expected_exact_trips,
        "exact_route_numbers": arguments.expected_exact_route_numbers,
        "exact_route_patterns": arguments.expected_exact_patterns,
    }
    if any(summary[key] != value for key, value in expected.items()):
        raise SystemExit("TIMETABLE_EXACT_AUDIT_COUNT_DRIFT")


def _assert_combined_counts(
    manifest: dict[str, object],
    arguments: argparse.Namespace,
) -> dict[str, int]:
    raw_trips = manifest.get("trip_rows")
    if not isinstance(raw_trips, list) or not all(isinstance(row, dict) for row in raw_trips):
        raise SystemExit("TIMETABLE_COMBINED_TRIPS_INVALID")
    trips = cast(list[dict[str, object]], raw_trips)
    patterns = {
        pattern
        for row in trips
        for pattern in cast(list[object], row.get("provider_route_pattern_ids", []))
        if isinstance(pattern, str)
    }
    route_numbers = {
        route_number
        for row in trips
        if isinstance((route_number := row.get("route_number")), str)
    }
    actual = {
        "trips": len(trips),
        "route_numbers": len(route_numbers),
        "patterns": len(patterns),
    }
    expected = {
        "trips": arguments.expected_combined_trips,
        "route_numbers": arguments.expected_combined_route_numbers,
        "patterns": arguments.expected_combined_patterns,
    }
    if actual != expected:
        raise SystemExit(f"TIMETABLE_COMBINED_AUDIT_COUNT_DRIFT:{actual}")
    return actual


def main() -> int:
    arguments = _arguments()
    if (arguments.base_bundle is None) != (arguments.expected_base_sha256 is None):
        raise SystemExit("TIMETABLE_INCREMENTAL_BASE_ARGUMENTS_INCOMPLETE")
    dsn = os.getenv("JEJU_IMPORTER_DSN")
    if not dsn:
        raise SystemExit("JEJU_IMPORTER_DSN_MISSING")
    checksums = read_workbook_checksums(arguments.checksum_file, arguments.input_dir)
    workbook_rows = load_verified_workbook_rows(arguments.input_dir, checksums)
    route_stops = _route_stops(dsn)
    result = map_exact_weekday_trips(
        workbook_rows,
        route_stops,
        target_date=arguments.target_date,
    )
    _assert_expected_counts(result, arguments)
    mapping_manifest = build_exact_mapping_manifest(
        workbook_rows,
        route_stops,
        result,
        official_snapshot_id=OFFICIAL_SNAPSHOT_ID,
    )
    east_workbooks = {
        "405009-route-201.xlsx": workbook_rows["405009.xlsx"],
        "405011-route-211-212.xlsx": workbook_rows["405011.xlsx"],
    }
    east_manifest = build_mapping_manifest(
        east_workbooks,
        tuple(item for item in route_stops if item.route_number in {"201", "211", "212"}),
        target_date=arguments.target_date,
    )
    mapping_manifest = merge_mapping_manifest_overlay(
        mapping_manifest,
        east_manifest,
        workbook_aliases={
            "405009-route-201.xlsx": "405009.xlsx",
            "405011-route-211-212.xlsx": "405011.xlsx",
        },
    )
    if arguments.base_bundle is not None:
        base_payload = arguments.base_bundle.read_bytes()
        if hashlib.sha256(base_payload).hexdigest() != arguments.expected_base_sha256:
            raise SystemExit("TIMETABLE_INCREMENTAL_BASE_CHECKSUM_MISMATCH")
        load_official_timetable_zip(io.BytesIO(base_payload))
        with zipfile.ZipFile(io.BytesIO(base_payload)) as base_archive:
            base_checksums = json.loads(base_archive.read("checksum-manifest.json"))
            base_mapping = json.loads(base_archive.read("mapping-manifest.json"))
        if base_checksums != {"algorithm": "SHA256", "files": checksums}:
            raise SystemExit("TIMETABLE_INCREMENTAL_BASE_WORKBOOK_DRIFT")
        if not isinstance(base_mapping, dict):
            raise SystemExit("TIMETABLE_INCREMENTAL_BASE_MANIFEST_INVALID")
        mapping_manifest = merge_incremental_mapping_manifest(
            cast(dict[str, object], base_mapping), mapping_manifest
        )
    combined_counts = _assert_combined_counts(mapping_manifest, arguments)
    mapping_manifest["mapping_audit"] = {
        "global_exact": result.summary(),
        "east_overlay_trip_rows": len(cast(list[object], east_manifest["trip_rows"])),
        "combined": combined_counts,
    }
    checksum_manifest = {"algorithm": "SHA256", "files": checksums}
    service_day_manifest = {
        "weekday_source_references": [
            "https://www.data.go.kr/data/3043887/fileData.do",
            "https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule",
        ],
        "holiday_source_references": list(
            dict.fromkeys(arguments.holiday_source_reference)
        ),
        "notice_source_references": ["https://bus.jeju.go.kr/notice/list"],
        "notices_reviewed_through": arguments.notices_reviewed_through.isoformat(),
        "service_exceptions": [],
        "notices": [],
        "route_exceptions": [],
    }
    workbook_payloads = {
        filename: (arguments.input_dir / filename).read_bytes() for filename in sorted(checksums)
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for filename, payload in workbook_payloads.items():
            _write_member(
                archive,
                filename,
                payload,
                target_date=arguments.target_date,
            )
        for name, value in (
            ("checksum-manifest.json", checksum_manifest),
            ("mapping-manifest.json", mapping_manifest),
            ("service-day-manifest.json", service_day_manifest),
        ):
            _write_member(
                archive,
                name,
                _json(value),
                target_date=arguments.target_date,
            )
    payload = raw.getvalue()
    official = load_official_timetable_zip(io.BytesIO(payload))
    bundle = build_weekday_timetable_bundle(official, arguments.target_date)
    validate_timetable_bundle(bundle)

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
                "official_snapshot_id": OFFICIAL_SNAPSHOT_ID,
                "workbooks": len(workbook_payloads),
                "route_stop_rows": len(cast(list[object], mapping_manifest["route_stops"])),
                "trip_rows": len(bundle.trips),
                "stop_time_rows": len(bundle.stop_times),
                "mapping_audit": result.summary(),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
