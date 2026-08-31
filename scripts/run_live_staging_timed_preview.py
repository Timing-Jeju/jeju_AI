"""TMAP 요약 fact를 메모리에서만 사용해 세 시간 일정 preview를 출력한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import psycopg

from jeju_trip.application.staging_timed_preview import (
    TimedPreviewPlace,
    TimedRouteFact,
    build_staging_timed_preview,
)
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.infrastructure.tmap_adapter import (
    DrivingRouteFact,
    HttpxJsonTransport,
    SafeRouteLog,
    TmapAdapter,
    normalize_tmap_driving,
)
from jeju_trip.infrastructure.tmap_cache import EphemeralRouteCache, RouteCacheKey
from jeju_trip.planning.policy import (
    estimate_taxi_fare,
    load_planning_policy,
    load_taxi_fare_policy,
)

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
DEFAULT_TRIP_DATE = date(2026, 8, 15)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"STAGING_ENV_MISSING:{name}")
    return value


def _preview_places(document: dict[str, Any]) -> tuple[str, dict[str, tuple[str, ...]]]:
    accommodation_id = str(document["accommodation"]["place_fact_id"])
    orders = {
        str(candidate["strategy"]): tuple(
            str(place["place_fact_id"]) for place in candidate["places"]
        )
        for candidate in document["candidate_orders"]
    }
    return accommodation_id, orders


def _active_places(fact_ids: set[str]) -> dict[str, dict[str, Any]]:
    with psycopg.connect(_required("JEJU_RUNTIME_DSN")) as connection:
        rows = connection.execute(
            """
            SELECT fact_id, name, category, attributes->>'category_level_3',
                   ST_Y(position::geometry), ST_X(position::geometry)
            FROM travel_read.active_place WHERE fact_id = ANY(%s)
            """,
            (list(fact_ids),),
        ).fetchall()
    places = {
        str(row[0]): {
            "place_fact_id": str(row[0]),
            "name": str(row[1]),
            "category": str(row[2]),
            "category_level_3": str(row[3] or ""),
            "latitude": float(row[4]),
            "longitude": float(row[5]),
        }
        for row in rows
    }
    if set(places) != fact_ids:
        raise ValueError("STAGING_TIMED_ACTIVE_PLACE_MISSING")
    return places


def _timed_place(place: dict[str, Any]) -> TimedPreviewPlace:
    return TimedPreviewPlace(
        place_fact_id=place["place_fact_id"],
        name=place["name"],
        category=place["category"],
        stay_policy_key=("cafe" if place["category_level_3"] == "A05020900" else None),
        activity_type=place.get("activity_type", "visit"),
    )


def _fact_id(prefix: str, origin: str, destination: str, activity_start: datetime) -> str:
    raw = f"{origin}|{destination}|{activity_start.isoformat()}".encode()
    digest = hashlib.sha256(raw).hexdigest()
    return f"{prefix}:{digest[:20]}"


def _fetch_edges(
    places: dict[str, dict[str, Any]],
    edges: set[tuple[str, str]],
    activity_start: datetime,
) -> tuple[dict[tuple[str, str], TimedRouteFact], list[SafeRouteLog]]:
    if len(edges) > 24:
        raise ValueError("STAGING_TMAP_CALL_BUDGET_EXCEEDED")
    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tmap.driving")
    policy = load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml")
    policy_fact_id = "jeju.taxi-fare-policy:2024-07-01:STANDARD"
    cache = EphemeralRouteCache[DrivingRouteFact](30 * 60)
    logs: list[SafeRouteLog] = []
    results: dict[tuple[str, str], TimedRouteFact] = {}
    with httpx.Client(timeout=12) as client:
        transport = HttpxJsonTransport(client)
        for origin_id, destination_id in sorted(edges):
            origin = places[origin_id]
            destination = places[destination_id]
            route_fact_id = _fact_id("tmap.driving", origin_id, destination_id, activity_start)
            adapter = TmapAdapter(
                contract,
                cache,
                transport,
                partial(
                    normalize_tmap_driving,
                    origin_entrance_id=f"provisional-center:{origin_id}",
                    destination_entrance_id=f"provisional-center:{destination_id}",
                    route_fact_id=route_fact_id,
                ),
                _required("JEJU_TMAP_API_KEY"),
                logs.append,
            )
            key = RouteCacheKey.build(
                "tmap.driving",
                (origin["latitude"], origin["longitude"]),
                (destination["latitude"], destination["longitude"]),
                activity_start.isoformat(timespec="minutes"),
                {"taxi"},
                "not_applicable",
                "0.6.0-staging-timed-preview",
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
            except Exception as error:
                raise ValueError("STAGING_TMAP_ROUTE_UNAVAILABLE") from error
            fare = estimate_taxi_fare(
                policy,
                vehicle_type="STANDARD",
                distance_meters=route.distance_meters,
                duration_seconds=route.duration_seconds,
                departure_at=activity_start,
                route_fact_id=route_fact_id,
                policy_fact_id=policy_fact_id,
            )
            results[(origin_id, destination_id)] = TimedRouteFact(
                route_fact_id=route_fact_id,
                origin_place_id=origin_id,
                destination_place_id=destination_id,
                distance_meters=route.distance_meters,
                duration_seconds=route.duration_seconds,
                fare_min_krw=fare.minimum_krw,
                fare_max_krw=fare.maximum_krw,
                fare_fact_id=_fact_id(
                    "computed.taxi-fare", origin_id, destination_id, activity_start
                ),
                fare_policy_fact_id=policy_fact_id,
            )
    return results, logs


def _fetch_routes(
    places: dict[str, dict[str, Any]],
    orders: dict[str, tuple[str, ...]],
    accommodation_id: str,
    activity_start: datetime,
) -> tuple[dict[tuple[str, str], TimedRouteFact], list[SafeRouteLog]]:
    edges = {
        (origin, destination)
        for order in orders.values()
        for origin, destination in zip(
            (accommodation_id, *order), (*order, accommodation_id), strict=True
        )
    }
    return _fetch_edges(places, edges, activity_start)


def _parse_edges(values: list[str]) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for value in values:
        parts = value.split(",")
        if len(parts) != 2 or not all(parts):
            raise ValueError("STAGING_EDGE_ARGUMENT_INVALID")
        edges.add((parts[0], parts[1]))
    return edges


def _print_edge_summary(
    route_facts: dict[tuple[str, str], TimedRouteFact],
    places: dict[str, dict[str, Any]],
    call_count: int,
) -> None:
    print("status=staging_route_edge_preview")
    print("production_recommendation=false")
    print(f"tmap_calls={call_count}")
    for edge, fact in sorted(route_facts.items()):
        print(
            f"{places[edge[0]]['name']} -> {places[edge[1]]['name']} "
            f"duration_seconds={fact.duration_seconds} "
            f"distance_meters={fact.distance_meters} "
            f"taxi={fact.fare_min_krw}~{fact.fare_max_krw}KRW "
            f"facts={fact.route_fact_id},{fact.fare_fact_id}"
        )


def _print_summary(payload: dict[str, Any], call_count: int) -> None:
    print("status=staging_timed_preview")
    print("production_recommendation=false")
    print(f"tmap_calls={call_count}")
    for schedule in payload["schedules"]:
        timeline = schedule["timeline"]
        print(f"\n[{schedule['strategy']}] risk={schedule['overall_risk']}")
        for event in timeline:
            label = (
                event.get("place_name")
                or event.get("venue_status")
                or f"{event.get('from_place_id')} -> {event.get('to_place_id')}"
            )
            details = ""
            if event["type"] == "transfer":
                details = (
                    f" distance={event['distance_meters']}m"
                    f" fare={event['taxi_fare_min_krw']}~{event['taxi_fare_max_krw']}KRW"
                    f" facts={','.join(event['evidence_fact_ids'])}"
                )
            print(
                f"{event['start_at'][11:16]}-{event['end_at'][11:16]} "
                f"{event['type']} {label}{details}"
            )
        totals = schedule["totals"]
        print(
            "totals "
            f"return={timeline[-1]['end_at'][11:16]} "
            f"distance={totals['taxi_distance_meters']}m "
            f"taxi={totals['taxi_cost_min_krw']}~{totals['taxi_cost_max_krw']}KRW "
            f"taxi_pickup_buffer={totals['taxi_pickup_buffer_minutes']}min "
            f"walk=unverifiable unallocated={totals['unallocated_minutes']}min "
            f"utilization={totals['window_utilization_ratio']:.1%}"
        )
        print("issues=" + ",".join(schedule["issues"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-preview", type=Path, required=True)
    parser.add_argument("--trip-date", type=date.fromisoformat, default=DEFAULT_TRIP_DATE)
    parser.add_argument(
        "--edge",
        action="append",
        default=[],
        help="변경 구간만 조회할 때 FROM_PLACE_ID,TO_PLACE_ID 형식으로 반복 지정",
    )
    arguments = parser.parse_args()
    activity_start = datetime.combine(arguments.trip_date, time(9), tzinfo=KST)
    activity_end = datetime.combine(arguments.trip_date, time(19), tzinfo=KST)
    document = json.loads(arguments.candidate_preview.read_text(encoding="utf-8"))
    accommodation_id, order_ids = _preview_places(document)
    fact_ids = {accommodation_id, *(value for order in order_ids.values() for value in order)}
    edges: set[tuple[str, str]] = set()
    if arguments.edge:
        edges = _parse_edges(arguments.edge)
        fact_ids.update(place_id for edge in edges for place_id in edge)
    places = _active_places(fact_ids)
    activity_types = {
        str(place["place_fact_id"]): str(place.get("activity_type", "visit"))
        for candidate in document["candidate_orders"]
        for place in candidate["places"]
    }
    for place_id, activity_type in activity_types.items():
        places[place_id]["activity_type"] = activity_type
    if arguments.edge:
        route_facts, logs = _fetch_edges(places, edges, activity_start)
        _print_edge_summary(route_facts, places, len(logs))
        return
    route_facts, logs = _fetch_routes(places, order_ids, accommodation_id, activity_start)
    candidate_orders = {
        strategy: tuple(_timed_place(places[place_id]) for place_id in order)
        for strategy, order in order_ids.items()
    }
    opening_status = {
        str(place["place_fact_id"]): str(place["opening_hours_status"])
        for candidate in document["candidate_orders"]
        for place in candidate["places"]
    }
    payload = build_staging_timed_preview(
        accommodation=_timed_place(places[accommodation_id]),
        candidate_orders=candidate_orders,
        route_facts=route_facts,
        opening_hours_status=opening_status,
        activity_start=activity_start,
        activity_end=activity_end,
        policy=load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).as_dict()
    _print_summary(payload, len(logs))


if __name__ == "__main__":
    main()
