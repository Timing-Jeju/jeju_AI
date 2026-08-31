#!/usr/bin/env python3
"""owner-only 공식 workbook의 평일 운행행과 TAGO pattern exact mapping을 감사한다."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jeju_trip.infrastructure.official_timetable_mapping import (  # noqa: E402
    TimetableRouteStop,
    map_exact_weekday_trips,
)
from jeju_trip.infrastructure.official_timetable_scope import (  # noqa: E402
    audit_timetable_scope,
    load_verified_workbook_rows,
    read_workbook_checksums,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--checksum-file", type=Path, required=True)
    parser.add_argument("--target-date", type=date.fromisoformat, required=True)
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


def main() -> int:
    arguments = _arguments()
    dsn = os.getenv("JEJU_IMPORTER_DSN")
    if not dsn:
        raise SystemExit("JEJU_IMPORTER_DSN_MISSING")
    checksums = read_workbook_checksums(arguments.checksum_file, arguments.input_dir)
    workbooks = load_verified_workbook_rows(arguments.input_dir, checksums)
    route_stops = _route_stops(dsn)
    patterns_by_number: dict[str, set[str]] = defaultdict(set)
    for item in route_stops:
        patterns_by_number[item.route_number].add(item.provider_route_pattern_id)
    pattern_counts = {
        route_number: len(pattern_ids)
        for route_number, pattern_ids in patterns_by_number.items()
    }
    scope = audit_timetable_scope(workbooks, pattern_counts)
    mapping = map_exact_weekday_trips(
        workbooks,
        route_stops,
        target_date=arguments.target_date,
    )
    output = {
        "scope": scope.to_dict(),
        "mapping": mapping.summary(),
        "globally_exact": scope.scope_catalog_aligned
        and bool(mapping.mappings)
        and bool(mapping.summary()["all_weekday_rows_exact"]),
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if output["globally_exact"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
