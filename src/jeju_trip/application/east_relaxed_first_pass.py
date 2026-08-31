"""검증 공백을 숨기지 않는 제주 동부 relaxed 단일 first-pass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from jeju_trip.domain.models import TransportPreferences
from jeju_trip.planning.cost_time_balance import (
    CostTimeBalanceResult,
    TransportCandidate,
    select_cost_time_balance,
)

MAX_FIRST_PASS_ROUTE_CALLS = 28
FIRST_PASS_ROUTE_CALL_LIMITS = {
    "direct_walk": 7,
    "taxi_driving": 7,
    "bus_endpoint_walk": 14,
}


@dataclass
class FirstPassRouteCallBudget:
    """cache miss만 세는 first-pass 7구간 외부 경로 호출 예산."""

    total_calls: int = 0
    direct_walk_calls: int = 0
    taxi_driving_calls: int = 0
    bus_endpoint_walk_calls: int = 0

    def claim(
        self,
        kind: Literal["direct_walk", "taxi_driving", "bus_endpoint_walk"],
        *,
        cache_hit: bool = False,
    ) -> None:
        if cache_hit:
            return
        field = f"{kind}_calls"
        next_kind_count = getattr(self, field) + 1
        if next_kind_count > FIRST_PASS_ROUTE_CALL_LIMITS[kind]:
            raise ValueError(f"FIRST_PASS_{kind.upper()}_CALL_BUDGET_EXCEEDED")
        if self.total_calls + 1 > MAX_FIRST_PASS_ROUTE_CALLS:
            raise ValueError("FIRST_PASS_ROUTE_CALL_BUDGET_EXCEEDED")
        setattr(self, field, next_kind_count)
        self.total_calls += 1


@dataclass(frozen=True)
class FirstPassPlace:
    """활성 장소 fact와 정책 체류시간만 담는 first-pass 활동."""

    place_id: str
    name: str
    activity_type: Literal["accommodation", "visit", "meal", "rest"]
    stay_minutes: int
    required: bool = False
    optional: bool = False

    def __post_init__(self) -> None:
        if not self.place_id or not self.name or self.stay_minutes < 0:
            raise ValueError("FIRST_PASS_PLACE_INVALID")


@dataclass(frozen=True)
class EastRelaxedFirstPass:
    """정식 세 추천과 구분되는 메모리 전용 단일 결과."""

    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _candidate_by_mode(
    candidates: tuple[TransportCandidate, ...], mode: str
) -> TransportCandidate | None:
    return next((candidate for candidate in candidates if candidate.mode == mode), None)


def _render(
    *,
    accommodation: FirstPassPlace,
    places: tuple[FirstPassPlace, ...],
    segment_candidates: dict[tuple[str, str], tuple[TransportCandidate, ...]],
    activity_start: datetime,
    preferences: TransportPreferences,
) -> tuple[list[dict[str, Any]], list[CostTimeBalanceResult]]:
    timeline: list[dict[str, Any]] = []
    decisions: list[CostTimeBalanceResult] = []
    current = activity_start
    previous = accommodation
    destinations = (*places, accommodation)
    for sequence, destination in enumerate(destinations, 1):
        edge = (previous.place_id, destination.place_id)
        candidates = segment_candidates.get(edge)
        if not candidates:
            raise ValueError(f"FIRST_PASS_SEGMENT_CANDIDATES_MISSING:{edge[0]}:{edge[1]}")
        result = select_cost_time_balance(
            walk=_candidate_by_mode(candidates, "walk"),
            bus=_candidate_by_mode(candidates, "bus"),
            taxi=_candidate_by_mode(candidates, "taxi"),
            preferences=preferences,
        )
        decisions.append(result)
        selected = result.selected
        if selected.mode == "taxi":
            assert selected.pickup_buffer_minutes is not None
            buffer_end = current + timedelta(minutes=selected.pickup_buffer_minutes)
            timeline.append(
                {
                    "event_id": f"relaxed-taxi-pickup-{sequence}",
                    "type": "buffer",
                    "start_at": _iso(current),
                    "end_at": _iso(buffer_end),
                    "duration_minutes": selected.pickup_buffer_minutes,
                    "place_id": previous.place_id,
                    "reason_code": "TAXI_PICKUP_PLANNING_BUFFER_APPLIED",
                    "is_live_dispatch_estimate": False,
                    "evidence_fact_ids": list(selected.evidence_fact_ids),
                }
            )
            current = buffer_end
            assert selected.driving_minutes is not None
            transfer_minutes = selected.driving_minutes
        else:
            assert selected.door_to_door_minutes is not None
            transfer_minutes = selected.door_to_door_minutes
        transfer_end = current + timedelta(minutes=transfer_minutes)
        timeline.append(
            {
                "event_id": f"relaxed-transfer-{sequence}",
                "type": "transfer",
                "start_at": _iso(current),
                "end_at": _iso(transfer_end),
                "duration_minutes": transfer_minutes,
                "mode": selected.mode,
                "from_place_id": previous.place_id,
                "to_place_id": destination.place_id,
                "distance_meters": selected.distance_meters,
                "cost": selected.cost.model_dump(mode="json") if selected.cost else None,
                "alternatives": [
                    alternative.model_dump(mode="json") for alternative in result.alternatives
                ],
                "mode_decision": result.decision.model_dump(mode="json"),
                "evidence_fact_ids": list(selected.evidence_fact_ids),
            }
        )
        current = transfer_end
        if destination.activity_type != "accommodation":
            activity_end = current + timedelta(minutes=destination.stay_minutes)
            timeline.append(
                {
                    "event_id": f"relaxed-{destination.activity_type}-{sequence}",
                    "type": destination.activity_type,
                    "place_id": destination.place_id,
                    "place_name": destination.name,
                    "start_at": _iso(current),
                    "end_at": _iso(activity_end),
                    "duration_minutes": destination.stay_minutes,
                    "required": destination.required,
                    "optional": destination.optional,
                    "opening_hours_evaluation": "unverifiable",
                    "evidence_fact_ids": [f"policy.stay:{destination.place_id}"],
                }
            )
            current = activity_end
        previous = destination
    return timeline, decisions


def _end_at(timeline: list[dict[str, Any]]) -> datetime:
    return datetime.fromisoformat(timeline[-1]["end_at"])


def _repair_to_window(
    *,
    accommodation: FirstPassPlace,
    places: tuple[FirstPassPlace, ...],
    segment_candidates: dict[tuple[str, str], tuple[TransportCandidate, ...]],
    activity_start: datetime,
    activity_end: datetime,
    preferences: TransportPreferences,
) -> tuple[
    list[dict[str, Any]],
    list[CostTimeBalanceResult],
    tuple[str, ...],
    tuple[FirstPassPlace, ...],
]:
    working = places
    repairs: list[str] = []
    for action in ("shorten_beach", "shorten_ojo", "remove_dorrell", "done"):
        timeline, decisions = _render(
            accommodation=accommodation,
            places=working,
            segment_candidates=segment_candidates,
            activity_start=activity_start,
            preferences=preferences,
        )
        if _end_at(timeline) <= activity_end:
            return timeline, decisions, tuple(repairs), working
        if action == "shorten_beach":
            working = tuple(
                FirstPassPlace(**{**place.__dict__, "stay_minutes": 60})
                if place.name == "광치기해변" and place.stay_minutes > 60
                else place
                for place in working
            )
            repairs.append("SHORTEN_GWANGCHIGI_90_TO_60")
        elif action == "shorten_ojo":
            working = tuple(
                FirstPassPlace(**{**place.__dict__, "stay_minutes": 60})
                if place.name == "오조포구" and place.stay_minutes > 60
                else place
                for place in working
            )
            repairs.append("SHORTEN_OJO_90_TO_60")
        elif action == "remove_dorrell":
            working = tuple(place for place in working if place.name != "도렐 제주 본점")
            repairs.append("REMOVE_DORRELL")
    raise ValueError("FIRST_PASS_ACTIVITY_WINDOW_EXCEEDED")


def _totals(timeline: list[dict[str, Any]], activity_end: datetime) -> dict[str, Any]:
    taxi_min = taxi_max = bus_min = bus_max = 0
    distance = 0
    pickup = 0
    modes = {"walk": 0, "bus": 0, "taxi": 0}
    for event in timeline:
        if (
            event["type"] == "buffer"
            and event.get("reason_code") == "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
        ):
            pickup += event["duration_minutes"]
        if event["type"] != "transfer":
            continue
        modes[event["mode"]] += 1
        distance += event.get("distance_meters") or 0
        cost = event.get("cost")
        if cost and event["mode"] == "taxi":
            taxi_min += cost["min_krw"]
            taxi_max += cost["max_krw"]
        elif cost and event["mode"] == "bus":
            bus_min += cost["min_krw"]
            bus_max += cost["max_krw"]
    return {
        "selected_mode_counts": modes,
        "total_distance_meters": distance,
        "taxi_cost_min_krw": taxi_min,
        "taxi_cost_max_krw": taxi_max,
        "bus_cost_min_krw": bus_min,
        "bus_cost_max_krw": bus_max,
        "transport_cost_min_krw": taxi_min + bus_min,
        "transport_cost_max_krw": taxi_max + bus_max,
        "taxi_pickup_buffer_minutes": pickup,
        "hotel_return_slack_minutes": int((activity_end - _end_at(timeline)).total_seconds() // 60),
    }


def _revalidation(timeline: list[dict[str, Any]], activity_end: datetime) -> dict[str, Any]:
    first_activity = next(event for event in timeline if event["type"] in {"visit", "meal", "rest"})
    recovery_activity = next(
        (
            event
            for event in timeline
            if event["type"] == "visit"
            and event.get("optional") is True
            and event["duration_minutes"] >= 60
        ),
        None,
    )
    recovery_options: list[dict[str, Any]] = []
    if recovery_activity is not None:
        delayed_return = _end_at(timeline) + timedelta(minutes=20)
        recovered_return = delayed_return - timedelta(minutes=30)
        if recovered_return <= activity_end:
            recovery_options.append(
                {
                    "action": "SHORTEN_STAY",
                    "affected_event_ids": [recovery_activity["event_id"]],
                    "before_minutes": recovery_activity["duration_minutes"],
                    "after_minutes": recovery_activity["duration_minutes"] - 30,
                    "expected_return_at": _iso(recovered_return),
                    "schedule_window_fit": True,
                    "reason_codes": ["SYNTHETIC_20_MINUTE_DELAY", "RECOVERY_REVALIDATED"],
                    "timeline_change_is_separate": True,
                }
            )
    return {
        "normal": {
            "status": "on_schedule",
            "delay_minutes": 0,
            "observed_event_id": first_activity["event_id"],
        },
        "delay": {
            "status": "at_risk",
            "delay_minutes": 20,
            "observed_event_id": first_activity["event_id"],
            "recovery_options": recovery_options,
        },
        "realtime_dispatch_used": False,
    }


def build_east_relaxed_first_pass(
    *,
    accommodation: FirstPassPlace,
    places: tuple[FirstPassPlace, ...],
    segment_candidates: dict[tuple[str, str], tuple[TransportCandidate, ...]],
    activity_start: datetime,
    activity_end: datetime,
    preferences: TransportPreferences,
) -> EastRelaxedFirstPass:
    """Generate→Evaluate→synthetic Revalidate를 단일 비운영 결과로 연결한다."""

    if activity_start.tzinfo is None or activity_end <= activity_start:
        raise ValueError("FIRST_PASS_ACTIVITY_WINDOW_INVALID")
    try:
        timeline, decisions, repairs, used_places = _repair_to_window(
            accommodation=accommodation,
            places=places,
            segment_candidates=segment_candidates,
            activity_start=activity_start,
            activity_end=activity_end,
            preferences=preferences,
        )
    except ValueError as error:
        return EastRelaxedFirstPass(
            {
                "status": "insufficient_feasible_routes",
                "production_recommendation": False,
                "generate": {"status": "failed", "timeline": [], "reason_code": str(error)},
                "evaluate": {"status": "not_run", "schedule_window_fit": False},
                "revalidate": {"status": "not_run"},
                "persistence": "PROCESS_MEMORY_ONLY",
            }
        )
    totals = _totals(timeline, activity_end)
    for event in timeline:
        if event["type"] == "transfer":
            event["mode_decision"]["deadline_slack_minutes"] = totals["hotel_return_slack_minutes"]
    route_pattern_count = sum(
        1
        for result in decisions
        if any(item.mode == "bus" and item.route_id is not None for item in result.alternatives)
    )
    exact_bus_count = sum(
        1
        for result in decisions
        if any(item.mode == "bus" and item.status == "feasible" for item in result.alternatives)
    )
    return EastRelaxedFirstPass(
        {
            "status": "partial",
            "production_recommendation": False,
            "strategy": "relaxed",
            "activity_window": {"start_at": _iso(activity_start), "end_at": _iso(activity_end)},
            "generate": {
                "status": "generated",
                "timeline": timeline,
                "repairs_applied": list(repairs),
                "included_place_ids": [place.place_id for place in used_places],
                "totals": totals,
                "bus_route_pattern_candidate_count": route_pattern_count,
                "exact_stop_time_candidate_count": exact_bus_count,
            },
            "evaluate": {
                "status": "unverifiable",
                "overall_risk": "unknown",
                "schedule_window_fit": _end_at(timeline) <= activity_end,
                "timeline_consistent": all(
                    previous["end_at"] <= current["start_at"]
                    for previous, current in zip(timeline, timeline[1:], strict=False)
                ),
                "duration_totals_consistent": (
                    sum(event["duration_minutes"] for event in timeline)
                    == int((_end_at(timeline) - activity_start).total_seconds() // 60)
                ),
                "transport_cost_totals_consistent": (
                    totals["transport_cost_min_krw"]
                    == totals["taxi_cost_min_krw"] + totals["bus_cost_min_krw"]
                    and totals["transport_cost_max_krw"]
                    == totals["taxi_cost_max_krw"] + totals["bus_cost_max_krw"]
                ),
                "hotel_round_trip": (
                    timeline[0]["start_at"] == _iso(activity_start)
                    and timeline[-1]["to_place_id"] == accommodation.place_id
                ),
                "opening_hours_status": "unknown",
                "entrance_coverage": 0.0,
                "reason_codes": ["OPENING_HOURS_UNKNOWN", "ENTRANCE_UNVERIFIED"],
            },
            "revalidate": _revalidation(timeline, activity_end),
            "persistence": "PROCESS_MEMORY_ONLY",
        }
    )
