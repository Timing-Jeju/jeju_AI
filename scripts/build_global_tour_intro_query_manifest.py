#!/usr/bin/env python3
"""active 제주 장소 전체의 TourAPI 상세소개 query CSV를 private 경로에 만든다."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
from pathlib import Path

import psycopg

_CONTENT_ID = re.compile(r"^[0-9]+$")
_CONTENT_TYPES = {"12", "14", "15", "28", "32", "38", "39"}


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"GLOBAL_TOUR_INTRO_ENV_MISSING:{name}")
    return value


def _queries(dsn: str) -> tuple[tuple[str, str], ...]:
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            """SELECT replace(fact_id, 'tourapi.place:', ''),
                      attributes->>'content_type_id'
                 FROM travel_read.active_place
                WHERE source_id = 'tourapi.place'
                ORDER BY fact_id"""
        ).fetchall()
    queries = tuple((str(row[0]), str(row[1])) for row in rows)
    if len({content_id for content_id, _ in queries}) != len(queries):
        raise ValueError("GLOBAL_TOUR_INTRO_CONTENT_ID_DUPLICATED")
    if any(
        _CONTENT_ID.fullmatch(content_id) is None or content_type not in _CONTENT_TYPES
        for content_id, content_type in queries
    ):
        raise ValueError("GLOBAL_TOUR_INTRO_QUERY_INVALID")
    return queries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-places", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.expected_places <= 0:
        raise ValueError("GLOBAL_TOUR_INTRO_EXPECTED_PLACES_INVALID")
    queries = _queries(_required("JEJU_RUNTIME_DSN"))
    if len(queries) != arguments.expected_places:
        raise ValueError(f"GLOBAL_TOUR_INTRO_SCOPE_DRIFT:{len(queries)}")

    arguments.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(arguments.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("contentId", "contentTypeId"))
        writer.writerows(queries)
    with arguments.output.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    print(f"상태: Pass\nquery 수: {len(queries)}\nSHA-256: {digest.hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
