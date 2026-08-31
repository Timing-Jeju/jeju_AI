"""부분 실데이터 시간 일정 preview의 결정론적 산술 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jeju_trip.application.staging_timed_preview import (
    TimedPreviewPlace,
    TimedRouteFact,
    build_staging_timed_preview,
)
from jeju_trip.planning.policy import load_planning_policy

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))


def test_three_timed_previews_have_hotel_boundaries_meals_and_evidence() -> None:
    """세 후보는 숙소 왕복 시각·식사·비용 합계와 존재하는 fact 참조를 일관되게 가져야 한다."""

    hotel = TimedPreviewPlace("tourapi.place:hotel", "숙소", "32")
    required = TimedPreviewPlace("tourapi.place:required", "필수", "12")
    a = TimedPreviewPlace("tourapi.place:a", "해변", "12")
    meal = TimedPreviewPlace("tourapi.place:meal", "실제 식당", "39", activity_type="meal")
    rest = TimedPreviewPlace(
        "tourapi.place:rest",
        "실제 카페",
        "39",
        stay_policy_key="cafe",
        activity_type="rest",
    )
    c = TimedPreviewPlace("tourapi.place:c", "체험", "28")
    orders = {
        "balanced": (required, a, meal, rest),
        "relaxed": (required, meal, a, rest),
        "experience_max": (required, a, meal, c, rest),
    }
    edges = {
        (origin.place_fact_id, destination.place_fact_id)
        for order in orders.values()
        for origin, destination in zip((hotel, *order), (*order, hotel), strict=True)
    }
    routes = {
        edge: TimedRouteFact(
            route_fact_id=f"tmap.driving:{index}",
            origin_place_id=edge[0],
            destination_place_id=edge[1],
            distance_meters=10_000,
            duration_seconds=1_200,
            fare_min_krw=10_000,
            fare_max_krw=20_000,
            fare_fact_id=f"computed.taxi-fare:{index}",
            fare_policy_fact_id="jeju.taxi-fare-policy:2024-07-01:STANDARD",
        )
        for index, edge in enumerate(sorted(edges), 1)
    }
    payload = build_staging_timed_preview(
        accommodation=hotel,
        candidate_orders=orders,
        route_facts=routes,
        opening_hours_status={
            place.place_fact_id: "not_verified" for place in (required, a, meal, rest, c)
        },
        activity_start=datetime(2026, 8, 15, 9, tzinfo=KST),
        activity_end=datetime(2026, 8, 15, 21, tzinfo=KST),
        policy=load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).as_dict()

    assert payload["status"] == "staging_timed_preview"
    assert payload["production_recommendation"] is False
    assert len(payload["schedules"]) == 3
    fact_ids = {fact["fact_id"] for fact in payload["evidence_facts"]}
    for schedule in payload["schedules"]:
        timeline = schedule["timeline"]
        assert timeline[0]["start_at"] == "2026-08-15T09:00:00+09:00"
        assert timeline[0]["reason"] == "TAXI_PICKUP_PLANNING_BUFFER"
        assert timeline[1]["from_place_id"] == hotel.place_fact_id
        assert timeline[-1]["to_place_id"] == hotel.place_fact_id
        assert schedule["accommodation_departure_at"] == timeline[0]["start_at"]
        assert schedule["accommodation_return_at"] == timeline[-1]["end_at"]
        assert any(event["type"] == "meal" for event in timeline)
        assert any(event["type"] == "meal" and event["venue"] == "실제 식당" for event in timeline)
        assert any(event["type"] == "rest" and event["venue"] == "실제 카페" for event in timeline)
        assert not any(event.get("venue_status") == "unverified_buffer_only" for event in timeline)
        assert schedule["totals"]["taxi_cost_min_krw"] > 0
        assert "taxi_budget_krw" not in schedule["totals"]
        assert "taxi_budget_within_maximum" not in schedule["totals"]
        assert "TAXI_BUDGET_MAX_EXCEEDED" not in schedule["issues"]
        expected_pickup_minutes = (10 if schedule["strategy"] == "relaxed" else 5) * len(
            [event for event in timeline if event["type"] == "transfer"]
        )
        assert schedule["totals"]["taxi_pickup_buffer_minutes"] == expected_pickup_minutes
        assert all(
            event["duration_minutes"] == (10 if schedule["strategy"] == "relaxed" else 5)
            and event["is_live_dispatch_estimate"] is False
            for event in timeline
            if event.get("reason") == "TAXI_PICKUP_PLANNING_BUFFER"
        )
        if schedule["strategy"] == "relaxed":
            assert schedule["totals"]["meal_minutes"] == 60
            assert schedule["totals"]["rest_minutes"] == 30
        assert schedule["totals"]["available_window_minutes"] == 720
        assert schedule["totals"]["unallocated_minutes"] > 20
        assert schedule["totals"]["window_utilization_ratio"] < 1
        assert ("ACTIVITY_WINDOW_UNDERFILLED" in schedule["issues"]) is (
            schedule["totals"]["window_utilization_ratio"] < 0.9
        )
        assert schedule["totals"]["walking_distance_meters"] is None
        assert schedule["overall_risk"] == "unknown"
        assert all(
            evaluation["slack_minutes"] is None
            and evaluation["duration_minutes"] > 0
            and evaluation["distance_meters"] > 0
            for evaluation in schedule["segment_evaluations"]
        )
        assert all(
            event["opening_hours_conflict"] is None
            for event in timeline
            if event["type"] == "visit"
        )
        for previous, current in zip(timeline, timeline[1:], strict=False):
            assert previous["end_at"] <= current["start_at"]
        assert all(
            fact_id in fact_ids for event in timeline for fact_id in event["evidence_fact_ids"]
        )
