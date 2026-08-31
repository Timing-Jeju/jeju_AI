"""시간·비용 근거를 함께 보존하는 실용형 이동수단 선택 정책."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jeju_trip.domain.models import (
    CostRange,
    EndpointBasis,
    ModeDecision,
    RouteAlternativeSummary,
    TransportPreferences,
)


@dataclass(frozen=True)
class TransportCandidate:
    """한 구간을 같은 출발시각으로 조회한 수단별 근거."""

    mode: Literal["walk", "bus", "taxi"]
    door_to_door_minutes: int | None
    walking_minutes: int
    distance_meters: int | None
    cost: CostRange | None
    status: Literal["feasible", "unverifiable", "unavailable"]
    evidence_fact_ids: tuple[str, ...]
    wait_minutes: int | None = None
    pickup_buffer_minutes: int | None = None
    driving_minutes: int | None = None
    route_id: str | None = None
    route_number: str | None = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "feasible" and (
            self.door_to_door_minutes is None or self.door_to_door_minutes <= 0
        ):
            raise ValueError("FEASIBLE_TRANSPORT_DURATION_MISSING")
        if self.mode == "taxi" and self.status == "feasible":
            if self.pickup_buffer_minutes is None or self.driving_minutes is None:
                raise ValueError("TAXI_PICKUP_AND_DRIVING_REQUIRED")
            if self.door_to_door_minutes != self.pickup_buffer_minutes + self.driving_minutes:
                raise ValueError("TAXI_DOOR_TO_DOOR_MISMATCH")


@dataclass(frozen=True)
class CostTimeBalanceResult:
    """선택된 수단과 선택하지 않은 후보를 모두 보존한다."""

    selected: TransportCandidate
    alternatives: tuple[RouteAlternativeSummary, ...]
    decision: ModeDecision


def _midpoint(cost: CostRange | None) -> int | None:
    return None if cost is None else (cost.min_krw + cost.max_krw) // 2


def _summary(
    candidate: TransportCandidate,
    *,
    selected: bool,
    extra_reason_codes: tuple[str, ...] = (),
) -> RouteAlternativeSummary:
    return RouteAlternativeSummary(
        mode=candidate.mode,
        duration_minutes=candidate.door_to_door_minutes,
        walking_minutes=candidate.walking_minutes,
        cost=candidate.cost,
        status=candidate.status,
        distance_meters=candidate.distance_meters,
        wait_minutes=candidate.wait_minutes,
        pickup_buffer_minutes=candidate.pickup_buffer_minutes,
        driving_minutes=candidate.driving_minutes,
        route_id=candidate.route_id,
        route_number=candidate.route_number,
        selected=selected,
        reason_codes=(*candidate.reason_codes, *extra_reason_codes),
        evidence_fact_ids=candidate.evidence_fact_ids,
    )


def select_cost_time_balance(
    *,
    walk: TransportCandidate | None,
    bus: TransportCandidate | None,
    taxi: TransportCandidate | None,
    preferences: TransportPreferences,
    deadline_slack_minutes: int | None = None,
) -> CostTimeBalanceResult:
    """15분 도보 우선 뒤 버스 추가시간과 중간값 절약액으로 한 수단을 고른다."""

    if preferences.selection_policy != "cost_time_balance":
        raise ValueError("COST_TIME_BALANCE_POLICY_REQUIRED")
    candidates = tuple(item for item in (walk, bus, taxi) if item is not None)
    feasible = tuple(item for item in candidates if item.status == "feasible")
    if not feasible:
        raise ValueError("NO_EVIDENCE_BACKED_TRANSPORT")

    reason_codes: tuple[str, ...]
    unselected: dict[Literal["walk", "bus", "taxi"], tuple[str, ...]] = {}
    bus_extra: int | None = None
    midpoint_savings: int | None = None
    savings_min: int | None = None
    savings_max: int | None = None
    time_threshold_margin: int | None = None
    savings_threshold_margin: int | None = None

    if (
        walk is not None
        and walk.status == "feasible"
        and walk.door_to_door_minutes is not None
        and walk.door_to_door_minutes <= preferences.direct_walk_limit_minutes
    ):
        selected = walk
        reason_codes = ("WALK_WITHIN_15_MINUTES",)
    elif (
        bus is not None
        and bus.status == "feasible"
        and taxi is not None
        and taxi.status == "feasible"
    ):
        if bus.cost is None or taxi.cost is None:
            raise ValueError("BUS_TAXI_COST_EVIDENCE_REQUIRED")
        assert bus.door_to_door_minutes is not None
        assert taxi.door_to_door_minutes is not None
        bus_extra = bus.door_to_door_minutes - taxi.door_to_door_minutes
        bus_midpoint = _midpoint(bus.cost)
        taxi_midpoint = _midpoint(taxi.cost)
        assert bus_midpoint is not None and taxi_midpoint is not None
        midpoint_savings = taxi_midpoint - bus_midpoint
        savings_min = taxi.cost.min_krw - bus.cost.max_krw
        savings_max = taxi.cost.max_krw - bus.cost.min_krw
        time_threshold_margin = preferences.max_bus_extra_minutes - bus_extra
        savings_threshold_margin = midpoint_savings - preferences.min_bus_savings_krw
        if bus.door_to_door_minutes <= taxi.door_to_door_minutes and bus_midpoint < taxi_midpoint:
            selected = bus
            reason_codes = ("BUS_FASTER_AND_CHEAPER",)
        elif (
            bus_extra <= preferences.max_bus_extra_minutes
            and midpoint_savings >= preferences.min_bus_savings_krw
        ):
            selected = bus
            reason_codes = ("BUS_SAVES_AT_LEAST_5000_WITHIN_30_MINUTES",)
        else:
            selected = taxi
            if bus_extra > preferences.max_bus_extra_minutes:
                reason_codes = ("TAXI_BUS_TIME_PENALTY_EXCEEDS_30_MINUTES",)
            else:
                reason_codes = ("TAXI_BUS_SAVINGS_BELOW_5000_KRW",)
    elif taxi is not None and taxi.status == "feasible":
        selected = taxi
        reason_codes = (
            "BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED"
            if bus is not None and bus.status == "unverifiable"
            else "TAXI_ONLY_VERIFIABLE_MOTORIZED_OPTION",
        )
    elif walk is not None and walk.status == "feasible":
        selected = walk
        reason_codes = ("WALK_ONLY_VERIFIABLE_OPTION",)
    elif bus is not None and bus.status == "feasible":
        selected = bus
        reason_codes = ("BUS_ONLY_VERIFIABLE_OPTION",)
    else:  # pragma: no cover - feasible tuple makes this unreachable
        raise ValueError("NO_EVIDENCE_BACKED_TRANSPORT")

    if selected.mode == "taxi":
        reason_codes = (*reason_codes, "TAXI_PICKUP_PLANNING_BUFFER_APPLIED")
    selection_near_threshold = (
        time_threshold_margin is not None
        and savings_threshold_margin is not None
        and (
            abs(time_threshold_margin) <= 5
            or abs(savings_threshold_margin) <= 1_000
        )
    )
    if selection_near_threshold:
        reason_codes = (*reason_codes, "BUS_SELECTION_NEAR_THRESHOLD")
    if bus is not None and bus.status == "unverifiable":
        unselected["bus"] = ("BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED",)

    all_fact_ids = tuple(
        dict.fromkeys(fact_id for item in candidates for fact_id in item.evidence_fact_ids)
    )
    if not all_fact_ids:
        raise ValueError("TRANSPORT_EVIDENCE_FACTS_REQUIRED")
    alternatives = tuple(
        _summary(
            item,
            selected=item is selected,
            extra_reason_codes=unselected.get(item.mode, ()),
        )
        for item in candidates
    )
    decision = ModeDecision(
        policy_id="cost-time-balance-v1",
        selected_mode=selected.mode,
        origin_basis=EndpointBasis.VERIFIED_ENTRANCE,
        destination_basis=EndpointBasis.VERIFIED_ENTRANCE,
        walk_threshold_minutes=preferences.direct_walk_limit_minutes,
        bus_wait_threshold_minutes=preferences.bus_wait_limit_minutes,
        max_bus_extra_minutes=preferences.max_bus_extra_minutes,
        min_bus_savings_krw=preferences.min_bus_savings_krw,
        planned_walk_minutes=walk.door_to_door_minutes if walk is not None else None,
        bus_wait_minutes=bus.wait_minutes if bus is not None else None,
        bus_door_to_door_minutes=bus.door_to_door_minutes if bus is not None else None,
        taxi_pickup_buffer_minutes=(taxi.pickup_buffer_minutes if taxi is not None else None),
        taxi_driving_minutes=taxi.driving_minutes if taxi is not None else None,
        taxi_door_to_door_minutes=(taxi.door_to_door_minutes if taxi is not None else None),
        bus_cost=bus.cost if bus is not None else None,
        taxi_cost=taxi.cost if taxi is not None else None,
        cost_midpoint_savings_krw=midpoint_savings,
        cost_savings_min_krw=savings_min,
        cost_savings_max_krw=savings_max,
        bus_extra_minutes=bus_extra,
        bus_time_threshold_margin_minutes=time_threshold_margin,
        bus_savings_threshold_margin_krw=savings_threshold_margin,
        selection_near_threshold=selection_near_threshold,
        deadline_slack_minutes=deadline_slack_minutes,
        reason_codes=reason_codes,
        unselected_reason_codes=unselected,
        evidence_fact_ids=all_fact_ids,
    )
    return CostTimeBalanceResult(selected=selected, alternatives=alternatives, decision=decision)
