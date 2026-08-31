"""route fact와 versioned 정책으로만 만드는 비운영 시간 일정 preview."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from jeju_trip.planning.policy import PlanningPolicy

STRATEGIES = ("balanced", "relaxed", "experience_max")


@dataclass(frozen=True)
class TimedPreviewPlace:
    place_fact_id: str
    name: str
    category: str
    stay_policy_key: str | None = None
    activity_type: Literal["visit", "meal", "rest"] = "visit"


@dataclass(frozen=True)
class TimedRouteFact:
    route_fact_id: str
    origin_place_id: str
    destination_place_id: str
    distance_meters: int
    duration_seconds: int
    fare_min_krw: int
    fare_max_krw: int
    fare_fact_id: str
    fare_policy_fact_id: str

    def __post_init__(self) -> None:
        if (
            self.distance_meters <= 0
            or self.duration_seconds <= 0
            or self.fare_min_krw < 0
            or self.fare_max_krw < self.fare_min_krw
        ):
            raise ValueError("STAGING_ROUTE_FACT_INVALID")


@dataclass(frozen=True)
class StagingTimedPreview:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


def _stay_key(place: TimedPreviewPlace) -> str:
    if place.stay_policy_key:
        return place.stay_policy_key
    if place.category == "14":
        return "museum_or_exhibition"
    if place.category in {"15", "28"}:
        return "experience_or_theme"
    if place.category == "38":
        return "market_or_shopping"
    if place.category == "39":
        return "restaurant"
    return "viewpoint_or_beach"


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def build_staging_timed_preview(
    *,
    accommodation: TimedPreviewPlace,
    candidate_orders: Mapping[str, tuple[TimedPreviewPlace, ...]],
    route_facts: Mapping[tuple[str, str], TimedRouteFact],
    opening_hours_status: Mapping[str, str],
    activity_start: datetime,
    activity_end: datetime,
    policy: PlanningPolicy,
) -> StagingTimedPreview:
    """시간 산술을 검증하되 미확정 출입구·운영시간 때문에 운영 성공을 주장하지 않는다."""

    if set(candidate_orders) != set(STRATEGIES):
        raise ValueError("STAGING_TIMED_STRATEGIES_INVALID")
    if activity_start.tzinfo is None or activity_end <= activity_start:
        raise ValueError("STAGING_ACTIVITY_WINDOW_INVALID")
    evidence: dict[str, dict[str, Any]] = {}
    schedules: list[dict[str, Any]] = []
    policy_prefix = f"policy.planning:{policy.policy_version}"

    for strategy in STRATEGIES:
        order = candidate_orders[strategy]
        timeline: list[dict[str, Any]] = []
        current = activity_start
        previous = accommodation
        taxi_distance = 0
        taxi_min = 0
        taxi_max = 0
        visit_minutes = 0
        meal_minutes = 0
        rest_minutes = 0
        taxi_pickup_buffer_total = 0
        meal_inserted = False
        rest_inserted = False
        pickup_minutes = getattr(policy.taxi_pickup_buffer_minutes, strategy)
        pickup_policy_fact_id = f"{policy_prefix}:taxi-pickup:{strategy}"
        evidence[pickup_policy_fact_id] = {
            "fact_id": pickup_policy_fact_id,
            "category": "taxi_pickup_planning_buffer_policy",
            "duration_minutes": pickup_minutes,
            "is_live_dispatch_estimate": False,
        }

        for sequence, place in enumerate(order, 1):
            edge = (previous.place_fact_id, place.place_fact_id)
            route = route_facts.get(edge)
            if route is None:
                raise ValueError(f"STAGING_ROUTE_FACT_MISSING:{edge[0]}:{edge[1]}")
            pickup_end = current + timedelta(minutes=pickup_minutes)
            timeline.append(
                {
                    "event_id": f"{strategy}-buffer-taxi-pickup-{sequence}",
                    "type": "buffer",
                    "start_at": _iso(current),
                    "end_at": _iso(pickup_end),
                    "duration_minutes": pickup_minutes,
                    "reason": "TAXI_PICKUP_PLANNING_BUFFER",
                    "is_live_dispatch_estimate": False,
                    "evidence_fact_ids": [pickup_policy_fact_id],
                }
            )
            taxi_pickup_buffer_total += pickup_minutes
            current = pickup_end
            route_minutes = math.ceil(route.duration_seconds / 60)
            transfer_end = current + timedelta(minutes=route_minutes)
            evidence[route.route_fact_id] = {
                "fact_id": route.route_fact_id,
                "category": "driving_route",
                "source_id": "tmap.driving",
                "endpoint_basis": "provisional_place_center",
                "distance_meters": route.distance_meters,
                "duration_seconds": route.duration_seconds,
                "expires_with_process": True,
            }
            evidence[route.fare_fact_id] = {
                "fact_id": route.fare_fact_id,
                "category": "taxi_fare_range",
                "source_id": "jeju.taxi-fare-policy",
                "input_fact_ids": [route.route_fact_id, route.fare_policy_fact_id],
                "minimum_krw": route.fare_min_krw,
                "maximum_krw": route.fare_max_krw,
                "is_estimated": True,
            }
            evidence[route.fare_policy_fact_id] = {
                "fact_id": route.fare_policy_fact_id,
                "category": "taxi_fare_policy",
                "source_id": "jeju.taxi-fare-policy",
            }
            timeline.append(
                {
                    "event_id": f"{strategy}-transfer-{sequence}",
                    "type": "transfer",
                    "start_at": _iso(current),
                    "end_at": _iso(transfer_end),
                    "duration_minutes": route_minutes,
                    "mode": "taxi",
                    "from_place_id": previous.place_fact_id,
                    "to_place_id": place.place_fact_id,
                    "distance_meters": route.distance_meters,
                    "taxi_fare_min_krw": route.fare_min_krw,
                    "taxi_fare_max_krw": route.fare_max_krw,
                    "walking_distance_meters": None,
                    "risk": "unknown",
                    "evidence_fact_ids": [route.route_fact_id, route.fare_fact_id],
                }
            )
            taxi_distance += route.distance_meters
            taxi_min += route.fare_min_krw
            taxi_max += route.fare_max_krw
            current = transfer_end

            stay_key = _stay_key(place)
            stay_policy = policy.stay_minutes[stay_key]
            if place.activity_type == "meal":
                if current.timetz().replace(tzinfo=None) < policy.meal_windows.lunch_start:
                    wait_end = datetime.combine(
                        current.date(), policy.meal_windows.lunch_start, tzinfo=current.tzinfo
                    )
                    wait_fact_id = f"{policy_prefix}:meal:lunch-window"
                    evidence[wait_fact_id] = {
                        "fact_id": wait_fact_id,
                        "category": "meal_window_policy",
                        "start_at": policy.meal_windows.lunch_start.isoformat(),
                    }
                    timeline.append(
                        {
                            "event_id": f"{strategy}-buffer-meal-window",
                            "type": "buffer",
                            "start_at": _iso(current),
                            "end_at": _iso(wait_end),
                            "duration_minutes": math.ceil(
                                (wait_end - current).total_seconds() / 60
                            ),
                            "reason": "MEAL_WINDOW_NOT_STARTED",
                            "evidence_fact_ids": [wait_fact_id],
                        }
                    )
                    current = wait_end
                duration_minutes = (
                    policy.meal_windows.default_duration_minutes
                    if strategy == "relaxed"
                    else max(policy.meal_windows.default_duration_minutes, stay_policy.maximum)
                )
                event_type = "meal"
                policy_fact_id = f"{policy_prefix}:meal:lunch"
            elif place.activity_type == "rest":
                duration_minutes = (
                    max(policy.rest.minimum_break_minutes, stay_policy.recommended)
                    if strategy == "relaxed" and not rest_inserted
                    else max(policy.rest.minimum_break_minutes, stay_policy.maximum)
                )
                event_type = "rest"
                policy_fact_id = f"{policy_prefix}:rest:venue"
            else:
                duration_minutes = stay_policy.maximum
                event_type = "visit"
                policy_fact_id = f"{policy_prefix}:stay:{stay_key}"
            hours_fact_id = f"staging.hours-status:{place.place_fact_id}"
            evidence[policy_fact_id] = {
                "fact_id": policy_fact_id,
                "category": f"{event_type}_duration_policy",
                "recommended_minutes": duration_minutes,
                "is_estimated": True,
            }
            evidence[hours_fact_id] = {
                "fact_id": hours_fact_id,
                "category": "opening_hours_coverage",
                "status": opening_hours_status.get(place.place_fact_id, "not_available"),
            }
            activity_end_at = current + timedelta(minutes=duration_minutes)
            activity_event = {
                "event_id": f"{strategy}-{event_type}-{sequence}",
                "type": event_type,
                "place_id": place.place_fact_id,
                "place_name": place.name,
                "arrive_at": _iso(current),
                "entry_at": _iso(current),
                "depart_at": _iso(activity_end_at),
                "start_at": _iso(current),
                "end_at": _iso(activity_end_at),
                "duration_minutes": duration_minutes,
                "duration_is_estimated": True,
                "opening_hours_evaluation": "unverifiable",
                "opening_hours_conflict": None,
                "risk": "unknown",
                "evidence_fact_ids": [policy_fact_id, hours_fact_id],
            }
            if event_type in {"meal", "rest"}:
                activity_event["venue"] = place.name
                activity_event["at_place_id"] = place.place_fact_id
                activity_event["venue_status"] = "named_but_hours_unverified"
            timeline.append(activity_event)
            if event_type == "meal":
                meal_minutes += duration_minutes
                meal_inserted = True
            elif event_type == "rest":
                rest_minutes += duration_minutes
                rest_inserted = True
            else:
                visit_minutes += duration_minutes
            current = activity_end_at
            previous = place

        if not meal_inserted or not rest_inserted:
            raise ValueError("STAGING_NAMED_MEAL_OR_REST_MISSING")

        return_edge = (previous.place_fact_id, accommodation.place_fact_id)
        return_route = route_facts.get(return_edge)
        if return_route is None:
            raise ValueError(f"STAGING_ROUTE_FACT_MISSING:{return_edge[0]}:{return_edge[1]}")
        pickup_end = current + timedelta(minutes=pickup_minutes)
        timeline.append(
            {
                "event_id": f"{strategy}-buffer-taxi-pickup-return",
                "type": "buffer",
                "start_at": _iso(current),
                "end_at": _iso(pickup_end),
                "duration_minutes": pickup_minutes,
                "reason": "TAXI_PICKUP_PLANNING_BUFFER",
                "is_live_dispatch_estimate": False,
                "evidence_fact_ids": [pickup_policy_fact_id],
            }
        )
        taxi_pickup_buffer_total += pickup_minutes
        current = pickup_end
        return_minutes = math.ceil(return_route.duration_seconds / 60)
        return_end = current + timedelta(minutes=return_minutes)
        evidence[return_route.route_fact_id] = {
            "fact_id": return_route.route_fact_id,
            "category": "driving_route",
            "source_id": "tmap.driving",
            "endpoint_basis": "provisional_place_center",
            "distance_meters": return_route.distance_meters,
            "duration_seconds": return_route.duration_seconds,
            "expires_with_process": True,
        }
        evidence[return_route.fare_fact_id] = {
            "fact_id": return_route.fare_fact_id,
            "category": "taxi_fare_range",
            "source_id": "jeju.taxi-fare-policy",
            "input_fact_ids": [
                return_route.route_fact_id,
                return_route.fare_policy_fact_id,
            ],
            "minimum_krw": return_route.fare_min_krw,
            "maximum_krw": return_route.fare_max_krw,
            "is_estimated": True,
        }
        evidence[return_route.fare_policy_fact_id] = {
            "fact_id": return_route.fare_policy_fact_id,
            "category": "taxi_fare_policy",
            "source_id": "jeju.taxi-fare-policy",
        }
        timeline.append(
            {
                "event_id": f"{strategy}-transfer-return",
                "type": "transfer",
                "start_at": _iso(current),
                "end_at": _iso(return_end),
                "duration_minutes": return_minutes,
                "mode": "taxi",
                "from_place_id": previous.place_fact_id,
                "to_place_id": accommodation.place_fact_id,
                "distance_meters": return_route.distance_meters,
                "taxi_fare_min_krw": return_route.fare_min_krw,
                "taxi_fare_max_krw": return_route.fare_max_krw,
                "walking_distance_meters": None,
                "risk": "unknown",
                "evidence_fact_ids": [return_route.route_fact_id, return_route.fare_fact_id],
            }
        )
        taxi_distance += return_route.distance_meters
        taxi_min += return_route.fare_min_krw
        taxi_max += return_route.fare_max_krw
        return_slack = math.floor((activity_end - return_end).total_seconds() / 60)
        available_minutes = math.ceil((activity_end - activity_start).total_seconds() / 60)
        scheduled_minutes = math.ceil((return_end - activity_start).total_seconds() / 60)
        unallocated_minutes = max(0, return_slack)
        transfer_events = [event for event in timeline if event["type"] == "transfer"]
        issues = [
            "ENTRANCE_UNVERIFIED",
            "OPENING_HOURS_UNKNOWN",
            "TAXI_DISPATCH_NOT_GUARANTEED",
            "WALKING_UNVERIFIABLE",
        ]
        if scheduled_minutes / available_minutes < 0.9:
            issues.append("ACTIVITY_WINDOW_UNDERFILLED")
        schedules.append(
            {
                "strategy": strategy,
                "status": "unverifiable",
                "overall_risk": "unknown" if return_slack >= 0 else "critical",
                "accommodation_departure_at": _iso(activity_start),
                "accommodation_return_at": _iso(return_end),
                "timeline": timeline,
                "segment_evaluations": [
                    {
                        "event_id": event["event_id"],
                        "mode": event["mode"],
                        "duration_minutes": event["duration_minutes"],
                        "distance_meters": event["distance_meters"],
                        "risk": "unknown",
                        "reason_code": "ENTRANCE_UNVERIFIED",
                        "slack_minutes": None,
                        "evidence_fact_ids": event["evidence_fact_ids"],
                    }
                    for event in transfer_events
                ],
                "issues": issues,
                "totals": {
                    "available_window_minutes": available_minutes,
                    "scheduled_minutes": scheduled_minutes,
                    "unallocated_minutes": unallocated_minutes,
                    "window_utilization_ratio": scheduled_minutes / available_minutes,
                    "visit_minutes": visit_minutes,
                    "meal_minutes": meal_minutes,
                    "rest_minutes": rest_minutes,
                    "taxi_distance_meters": taxi_distance,
                    "taxi_cost_min_krw": taxi_min,
                    "taxi_cost_max_krw": taxi_max,
                    "taxi_pickup_buffer_minutes": taxi_pickup_buffer_total,
                    "walking_distance_meters": None,
                    "walking_minutes": None,
                    "hotel_return_slack_minutes": return_slack,
                },
            }
        )

    return StagingTimedPreview(
        {
            "status": "staging_timed_preview",
            "production_recommendation": False,
            "activity_window": {"start_at": _iso(activity_start), "end_at": _iso(activity_end)},
            "accommodation": {
                "place_fact_id": accommodation.place_fact_id,
                "name": accommodation.name,
                "endpoint_status": "provisional_center_only",
            },
            "schedules": schedules,
            "evidence_facts": list(evidence.values()),
            "persistence": "PROCESS_MEMORY_ONLY",
        }
    )
