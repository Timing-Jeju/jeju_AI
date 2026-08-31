"""제주 동부 relaxed 단일 first-pass 연결 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from jeju_trip.application.east_relaxed_first_pass import (
    FirstPassPlace,
    FirstPassRouteCallBudget,
    build_east_relaxed_first_pass,
)
from jeju_trip.domain.models import CostRange, TransportPreferences
from jeju_trip.planning.cost_time_balance import TransportCandidate

KST = timezone(timedelta(hours=9))


def _candidate(
    mode: Literal["walk", "bus", "taxi"],
    minutes: int | None,
    *,
    status: Literal["feasible", "unverifiable", "unavailable"] = "feasible",
):
    pickup = 10 if mode == "taxi" else None
    driving = minutes - 10 if mode == "taxi" and minutes is not None else None
    return TransportCandidate(
        mode=mode,
        door_to_door_minutes=minutes,
        walking_minutes=minutes or 0 if mode == "walk" else 0,
        distance_meters=600,
        cost=CostRange(
            min_krw=0 if mode == "walk" else 1_200 if mode == "bus" else 7_000,
            max_krw=0 if mode == "walk" else 1_200 if mode == "bus" else 9_000,
            is_estimated=mode == "taxi",
        ),
        status=status,
        wait_minutes=8 if mode == "bus" and status == "feasible" else None,
        pickup_buffer_minutes=pickup,
        driving_minutes=driving,
        route_id="pattern-201" if mode == "bus" else None,
        route_number="201" if mode == "bus" else None,
        evidence_fact_ids=(f"fact-{mode}-{minutes}-{status}",),
    )


def test_first_pass_preserves_pickup_alternatives_evaluation_and_recovery() -> None:
    """단일 relaxed 결과는 대기·주행·대안과 평가·지연 회복안을 같은 타임라인에서 보존해야 한다."""

    hotel = FirstPassPlace("hotel", "플레이스 캠프 제주", "accommodation", 0)
    places = (
        FirstPassPlace("sunrise", "성산일출봉", "visit", 90, required=True),
        FirstPassPlace("ojo", "오조포구", "visit", 90, optional=True),
        FirstPassPlace("meal", "성산 부뚜막식당", "meal", 60),
        FirstPassPlace("beach", "광치기해변", "visit", 90, optional=True),
        FirstPassPlace("dorrell", "도렐 제주 본점", "rest", 30, optional=True),
        FirstPassPlace("horang", "호랑호랑", "rest", 60),
    )
    ids = (hotel.place_id, *(place.place_id for place in places), hotel.place_id)
    candidates = {}
    for index, edge in enumerate(zip(ids, ids[1:], strict=False)):
        walk_minutes = 12 if index >= 4 else 40
        candidates[edge] = (
            _candidate("walk", walk_minutes),
            _candidate("bus", None, status="unverifiable"),
            _candidate("taxi", 20),
        )

    result = build_east_relaxed_first_pass(
        accommodation=hotel,
        places=places,
        segment_candidates=candidates,
        activity_start=datetime(2026, 8, 14, 9, tzinfo=KST),
        activity_end=datetime(2026, 8, 14, 19, tzinfo=KST),
        preferences=TransportPreferences(selection_policy="cost_time_balance"),
    ).as_dict()

    assert result["production_recommendation"] is False
    assert result["status"] == "partial"
    assert result["generate"]["status"] == "generated"
    assert result["evaluate"]["status"] == "unverifiable"
    assert result["evaluate"]["schedule_window_fit"] is True
    assert result["revalidate"]["normal"]["status"] == "on_schedule"
    assert result["revalidate"]["delay"]["status"] == "at_risk"
    assert result["revalidate"]["delay"]["recovery_options"][0]["action"] == "SHORTEN_STAY"
    timeline = result["generate"]["timeline"]
    taxi_transfers = [
        event for event in timeline if event["type"] == "transfer" and event["mode"] == "taxi"
    ]
    assert taxi_transfers
    for transfer in taxi_transfers:
        previous = timeline[timeline.index(transfer) - 1]
        assert previous["type"] == "buffer"
        assert previous["reason_code"] == "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
        assert previous["end_at"] == transfer["start_at"]
        assert any(
            item["mode"] == "bus" and item["status"] == "unverifiable"
            for item in transfer["alternatives"]
        )
    assert any(event["type"] == "transfer" and event["mode"] == "walk" for event in timeline)


def test_first_pass_fails_without_any_evidence_backed_mode() -> None:
    """어느 수단도 근거를 갖지 못한 구간은 부분 일정 없이 first-pass 전체를 실패해야 한다."""

    hotel = FirstPassPlace("hotel", "숙소", "accommodation", 0)
    place = FirstPassPlace("sunrise", "성산일출봉", "visit", 90, required=True)
    unavailable = _candidate("bus", None, status="unavailable")

    result = build_east_relaxed_first_pass(
        accommodation=hotel,
        places=(place,),
        segment_candidates={
            ("hotel", "sunrise"): (unavailable,),
            ("sunrise", "hotel"): (unavailable,),
        },
        activity_start=datetime(2026, 8, 14, 9, tzinfo=KST),
        activity_end=datetime(2026, 8, 14, 19, tzinfo=KST),
        preferences=TransportPreferences(selection_policy="cost_time_balance"),
    ).as_dict()

    assert result["status"] == "insufficient_feasible_routes"
    assert result["generate"]["timeline"] == []


def test_long_route_first_pass_connects_all_three_lifecycle_stages() -> None:
    """함덕·월정리 first-pass는 생성·평가·지연 회복안을 같은 타임라인으로 완주해야 한다."""

    hotel = FirstPassPlace("hotel", "플레이스 캠프 제주", "accommodation", 0)
    places = (
        FirstPassPlace("sunrise", "성산일출봉", "visit", 60),
        FirstPassPlace("meal", "성산 식당", "meal", 60),
        FirstPassPlace("woljeong", "월정리해안도로", "visit", 90, optional=True),
        FirstPassPlace("hamdeok", "함덕해수욕장", "visit", 60, required=True),
        FirstPassPlace("rest", "동부 카페", "rest", 30),
    )
    ids = (hotel.place_id, *(place.place_id for place in places), hotel.place_id)
    candidates = {}
    for edge in zip(ids, ids[1:], strict=False):
        bus = (
            _candidate("bus", 35)
            if edge == ("woljeong", "hamdeok")
            else _candidate("bus", None, status="unverifiable")
        )
        candidates[edge] = (_candidate("walk", 50), bus, _candidate("taxi", 20))

    result = build_east_relaxed_first_pass(
        accommodation=hotel,
        places=places,
        segment_candidates=candidates,
        activity_start=datetime(2026, 8, 14, 9, tzinfo=KST),
        activity_end=datetime(2026, 8, 14, 19, tzinfo=KST),
        preferences=TransportPreferences(selection_policy="cost_time_balance"),
    ).as_dict()

    assert result["generate"]["status"] == "generated"
    assert {"woljeong", "hamdeok"} <= set(result["generate"]["included_place_ids"])
    assert any(
        event["type"] == "transfer" and event["mode"] == "bus"
        for event in result["generate"]["timeline"]
    )
    assert result["evaluate"]["timeline_consistent"] is True
    assert result["evaluate"]["schedule_window_fit"] is True
    recovery = result["revalidate"]["delay"]["recovery_options"][0]
    assert recovery["action"] == "SHORTEN_STAY"
    assert recovery["affected_event_ids"] == ["relaxed-visit-3"]
    assert recovery["schedule_window_fit"] is True


def test_first_pass_route_budget_excludes_cache_hits_and_caps_each_kind() -> None:
    """first-pass 경로 예산은 cache hit를 제외하고 도보·택시·버스 endpoint 상한을 지켜야 한다."""

    budget = FirstPassRouteCallBudget()
    for _ in range(7):
        budget.claim("direct_walk")
        budget.claim("taxi_driving")
        budget.claim("bus_endpoint_walk")
        budget.claim("bus_endpoint_walk")
    budget.claim("direct_walk", cache_hit=True)

    assert budget.total_calls == 28
