#!/usr/bin/env python3
"""owner-only 공식 workbook과 활성 TAGO 전역 노선 범위의 일치 여부를 감사한다."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jeju_trip.infrastructure.official_timetable_scope import (  # noqa: E402
    audit_timetable_scope,
    load_verified_workbook_rows,
    read_workbook_checksums,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--checksum-file", type=Path, required=True)
    return parser.parse_args()


def _active_route_pattern_counts(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            """SELECT route.route_number, count(*)
                 FROM travel_projection.bus_route_fact AS route
                 JOIN source_admin.active_snapshot AS active
                   ON active.publication_id = route.publication_id
                  AND active.source_id = 'tago.bus-route'
                GROUP BY route.route_number
                ORDER BY route.route_number"""
        ).fetchall()
    return {str(route_number): int(pattern_count) for route_number, pattern_count in rows}


def main() -> int:
    arguments = _arguments()
    dsn = os.getenv("JEJU_IMPORTER_DSN")
    if not dsn:
        raise SystemExit("JEJU_IMPORTER_DSN_MISSING")
    checksums = read_workbook_checksums(arguments.checksum_file, arguments.input_dir)
    audit = audit_timetable_scope(
        load_verified_workbook_rows(arguments.input_dir, checksums),
        _active_route_pattern_counts(dsn),
    )
    print(json.dumps(audit.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if audit.scope_catalog_aligned else 2


if __name__ == "__main__":
    raise SystemExit(main())
