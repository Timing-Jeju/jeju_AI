"""공식 TAGO active stop을 route-stop 좌표와 교차검증해 identity CSV로 생성한다."""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"STOP_IDENTITY_ENV_MISSING:{name}")
    return value


def build_identity_records(
    route_stops: Iterable[tuple[Any, ...]],
    active_stops: Iterable[tuple[Any, ...]],
) -> tuple[list[dict[str, object]], tuple[str, ...], tuple[str, ...]]:
    """공식 TAGO ID가 유일한 active 정류장을 route-stop 좌표와 교차검증한다."""

    route_positions: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for stop_fact_id, latitude, longitude in route_stops:
        route_positions[str(stop_fact_id)].append((float(latitude), float(longitude)))

    materialized_active = tuple(active_stops)
    active_fact_ids = [str(row[0]) for row in materialized_active]
    provider_stop_ids = [str(row[1]) for row in materialized_active]
    if len(active_fact_ids) != len(set(active_fact_ids)):
        raise ValueError("STOP_IDENTITY_ACTIVE_FACT_ID_DUPLICATED")
    if len(provider_stop_ids) != len(set(provider_stop_ids)):
        raise ValueError("STOP_IDENTITY_PROVIDER_STOP_ID_DUPLICATED")

    records: list[dict[str, object]] = []
    for row in sorted(materialized_active, key=lambda item: str(item[0])):
        stop_fact_id, provider_stop_id, name, direction_text, stop_latitude, stop_longitude = row
        positions = route_positions.get(str(stop_fact_id), ())
        if any(
            abs(route_latitude - float(stop_latitude)) > 0.00001
            or abs(route_longitude - float(stop_longitude)) > 0.00001
            for route_latitude, route_longitude in positions
        ):
            raise ValueError("STOP_IDENTITY_COORDINATE_MISMATCH")
        records.append(
            {
                "canonical_stop_id": f"jeju.stop:tago:{provider_stop_id}",
                "provider": "TAGO",
                "provider_stop_id": provider_stop_id,
                "source_fact_id": stop_fact_id,
                "latitude": stop_latitude,
                "longitude": stop_longitude,
                "normalized_name": name,
                "direction_text": direction_text or name,
                "mapping_method": "OFFICIAL_ID",
                "mapping_confidence": "1.0",
                "mapping_status": "CONFIRMED",
                "source_reference": "https://www.data.go.kr/data/15098529/openapi.do",
            }
        )

    active_id_set = set(active_fact_ids)
    missing_active = tuple(sorted(set(route_positions) - active_id_set))
    active_without_route = tuple(sorted(active_id_set - set(route_positions)))
    return records, missing_active, active_without_route


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-stop-publication", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    with psycopg.connect(_required("JEJU_IMPORTER_DSN")) as connection:
        route_stops = connection.execute(
            """SELECT DISTINCT stop_fact_id,
                      ST_Y(position::geometry), ST_X(position::geometry)
               FROM travel_projection.bus_route_stop
               WHERE publication_id = %s
               ORDER BY stop_fact_id""",
            (arguments.route_stop_publication,),
        ).fetchall()
    with psycopg.connect(_required("JEJU_RUNTIME_DSN")) as connection:
        active_stops = connection.execute(
            """SELECT fact_id, provider_stop_id, name, direction_text,
                      ST_Y(position::geometry), ST_X(position::geometry)
               FROM travel_read.active_bus_stop
               ORDER BY fact_id"""
        ).fetchall()

    records, missing_active, active_without_route = build_identity_records(
        route_stops, active_stops
    )
    if not records:
        raise ValueError("STOP_IDENTITY_ACTIVE_STOPS_EMPTY")

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(
        f"status=PASS records={len(records)} "
        f"inactive_route_stop_ids={len(missing_active)} "
        f"active_without_route_ids={len(active_without_route)}"
    )


if __name__ == "__main__":
    main()
