"""제주 대표 권역의 TMAP 연결 가능성을 비운영 조건으로 확인한다."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg

from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.infrastructure.tmap_adapter import (
    DrivingRouteFact,
    HttpxJsonTransport,
    SafeRouteLog,
    TmapAdapter,
    normalize_tmap_driving,
)
from jeju_trip.infrastructure.tmap_cache import EphemeralRouteCache, RouteCacheKey

ROOT = Path(__file__).resolve().parents[1]
REPRESENTATIVE_ANCHORS = (
    (33.543, 126.670),
    (33.460, 126.940),
    (33.245, 126.410),
    (33.394, 126.240),
)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"STAGING_ENV_MISSING:{name}")
    return value


def _places() -> list[dict[str, Any]]:
    with psycopg.connect(_required("JEJU_RUNTIME_DSN")) as connection:
        rows = connection.execute(
            """WITH anchor AS (
                 SELECT ordinal, latitude, longitude
                 FROM unnest(%s::double precision[], %s::double precision[])
                      WITH ORDINALITY AS value(latitude, longitude, ordinal)
               ), ranked AS (
                 SELECT anchor.ordinal, place.fact_id, place.name,
                        ST_Y(place.position::geometry), ST_X(place.position::geometry),
                        row_number() OVER (
                          PARTITION BY anchor.ordinal
                          ORDER BY ST_Distance(
                            place.position,
                            ST_SetSRID(
                              ST_MakePoint(anchor.longitude, anchor.latitude), 4326
                            )::geography
                          ), place.fact_id
                        ) AS candidate_rank
                 FROM anchor
                 CROSS JOIN travel_read.active_place place
                 CROSS JOIN travel_read.active_service_area_boundary boundary
                 WHERE place.category = '12'
                   AND ST_Covers(boundary.geometry, place.position::geometry)
               )
               SELECT ordinal, fact_id, name, st_y, st_x
               FROM ranked
               WHERE candidate_rank = 1
               ORDER BY ordinal""",
            (
                [anchor[0] for anchor in REPRESENTATIVE_ANCHORS],
                [anchor[1] for anchor in REPRESENTATIVE_ANCHORS],
            ),
        ).fetchall()
    places = [
        {
            "place_fact_id": str(row[1]),
            "name": str(row[2]),
            "latitude": float(row[3]),
            "longitude": float(row[4]),
        }
        for row in rows
    ]
    if len(places) != len(REPRESENTATIVE_ANCHORS) or len(
        {place["place_fact_id"] for place in places}
    ) != len(places):
        raise ValueError("STAGING_REPRESENTATIVE_PLACE_MISSING")
    return places


def _run_segment(
    index: int,
    origin: dict[str, Any],
    destination: dict[str, Any],
    transport: HttpxJsonTransport,
    logs: list[SafeRouteLog],
) -> dict[str, Any]:
    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tmap.driving")
    cache = EphemeralRouteCache[DrivingRouteFact](30 * 60)
    route_fact_id = f"staging-memory-only-route:{index}"
    adapter = TmapAdapter(
        contract,
        cache,
        transport,
        lambda raw: normalize_tmap_driving(
            raw,
            origin_entrance_id=f"provisional-center:{origin['place_fact_id']}",
            destination_entrance_id=f"provisional-center:{destination['place_fact_id']}",
            route_fact_id=route_fact_id,
        ),
        _required("JEJU_TMAP_API_KEY"),
        logs.append,
    )
    checked_at = datetime.now(UTC).replace(second=0, microsecond=0)
    key = RouteCacheKey.build(
        "tmap.driving",
        (origin["latitude"], origin["longitude"]),
        (destination["latitude"], destination["longitude"]),
        checked_at.isoformat(),
        {"taxi"},
        "not_applicable",
        "0.4.0-staging-poc",
    )
    try:
        route = adapter.fetch(
            key,
            {
                "startX": origin["longitude"],
                "startY": origin["latitude"],
                "endX": destination["longitude"],
                "endY": destination["latitude"],
                "reqCoordType": "WGS84GEO",
                "resCoordType": "WGS84GEO",
                "trafficInfo": "Y",
            },
        )
        passed = route.distance_meters > 0 and route.duration_seconds > 0
    except Exception:
        passed = False
    return {
        "from_place_fact_id": origin["place_fact_id"],
        "from_name": origin["name"],
        "to_place_fact_id": destination["place_fact_id"],
        "to_name": destination["name"],
        "status": "pass" if passed else "fail",
        "route_metrics_persisted": False,
    }


def _markdown(payload: dict[str, Any]) -> str:
    segments = "\n".join(
        f"- {item['from_name']} → {item['to_name']}: `{item['status']}`"
        for item in payload["segments"]
    )
    return f"""# 제주 대표 권역 TMAP staging PoC

생성시각: {payload["generated_at"]}

## 판정

- 상태: `{payload["status"]}`
- 통과 구간: {payload["passed_segments"]}/{payload["total_segments"]}
- 시작·종료점 기준: `place_center_not_verified_entrance`
- 운영 활성화 허용: `false`
- TMAP 수치·원본·geometry 저장: `false`

## 대표 연결 구간

{segments}

## 사용 조건

이 PoC는 승인된 `tmap.driving` 계약과 현재 API key가 제주 동부·남부·서부 대표 지점 사이에서
정상 응답하는지만 확인한다. 아직 검증된 차량 출입구가 없으므로 이동시간·거리·일정 가능성의
운영 근거로 사용할 수 없으며 `driving_routing_ready`를 활성화하지 않는다.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    logs: list[SafeRouteLog] = []
    places = _places()
    with httpx.Client(timeout=12) as client:
        transport = HttpxJsonTransport(client)
        segments = [
            _run_segment(index, origin, destination, transport, logs)
            for index, (origin, destination) in enumerate(zip(places, places[1:], strict=False), 1)
        ]
    passed = sum(segment["status"] == "pass" for segment in segments)
    payload = {
        "status": "pass" if passed == len(segments) else "fail",
        "source_id": "tmap.driving",
        "endpoint_basis": "place_center_not_verified_entrance",
        "total_segments": len(segments),
        "passed_segments": passed,
        "segments": segments,
        "safe_log_count": len(logs),
        "route_metrics_persisted": False,
        "production_activation_allowed": False,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    (arguments.output_dir / "tmap-corridor-poc.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (arguments.output_dir / "tmap-corridor-poc.md").write_text(_markdown(payload), encoding="utf-8")
    print(f"상태: {payload['status']}\n통과 구간: {passed}/{len(segments)}\n운영 활성화: false")
    if passed != len(segments):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
