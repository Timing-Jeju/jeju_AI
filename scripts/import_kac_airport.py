"""공식 공항 CSV를 checksum 확인 후 raw-first로 적재하고 선택적으로 게시한다."""

from __future__ import annotations

import argparse
import hashlib
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import psycopg

from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer
from jeju_trip.infrastructure.source_catalog import load_default_source_catalog
from jeju_trip.interfaces.cli import _manual_dependencies


def find_staged_airport_publication(dsn: str, checksum: str, source_date: date) -> UUID | None:
    """동일 승인 원본의 신선한 STAGED만 재개하며 오래된 관측 시각은 갱신하지 않는다."""
    with psycopg.connect(dsn) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            """SELECT p.publication_id FROM source_admin.publication p
               JOIN source_admin.acquisition a ON a.acquisition_id = p.acquisition_id
               WHERE p.source_id = 'kac.airport' AND a.source_id = 'kac.airport'
                 AND a.status = 'STAGED' AND a.raw_checksum = %s AND a.source_date = %s
                 AND a.observed_at >= now() - interval '30 days'
                 AND a.observed_at <= now() + interval '5 minutes'
               ORDER BY p.published_at DESC LIMIT 1""",
            (checksum, source_date),
        ).fetchone()
    return UUID(str(row[0])) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_csv", type=Path)
    parser.add_argument("--source-date", type=date.fromisoformat, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    source = load_default_source_catalog().require("kac.airport")
    if args.raw_csv.stat().st_size > source.acquisition.maximum_response_bytes:
        raise ValueError("AIRPORT_SOURCE_TOO_LARGE")
    if hashlib.sha256(args.raw_csv.read_bytes()).hexdigest() != args.sha256:
        raise ValueError("AIRPORT_SOURCE_CHECKSUM_MISMATCH")
    service, publisher = _manual_dependencies()
    outcome = service.import_airport(
        source, args.raw_csv, args.source_date, publisher.publish_places
    )
    print("AIRPORT_IMPORT", outcome.status, outcome.publication_id, flush=True)
    if not args.activate:
        return
    publication = (
        UUID(outcome.publication_id)
        if outcome.publication_id is not None
        else find_staged_airport_publication(
            os.environ["JEJU_IMPORTER_DSN"], args.sha256, args.source_date
        )
    )
    if publication is None:
        raise ValueError("AIRPORT_FRESH_STAGED_PUBLICATION_REQUIRED")
    today = datetime.now(UTC).date()
    record = PostgresCoverageMeasurer(
        os.environ["JEJU_IMPORTER_DSN"], assume_role="jeju_importer"
    ).measure(
        publication,
        "airport_anchor_ready",
        today,
        today + timedelta(days=source.temporal.freshness_days - 1),
        "JEJU_ALL",
        "ALL",
    )
    if record.coverage_ratio != 1:
        raise ValueError("AIRPORT_BOUNDARY_VALIDATION_FAILED")
    publisher.publish_coverage(publication, (record,))
    print("AIRPORT_ACTIVE", publication, flush=True)


if __name__ == "__main__":
    main()
