"""실용형 수단 비교 정책 테스트."""

from __future__ import annotations

from typing import Literal

import pytest

from jeju_trip.domain.models import CostRange, TransportPreferences
from jeju_trip.planning.cost_time_balance import (
    TransportCandidate,
    select_cost_time_balance,
)


def _candidate(
    mode: Literal["walk", "bus", "taxi"],
    duration: int | None,
    minimum: int,
    maximum: int,
    *,
    status: Literal["feasible", "unverifiable", "unavailable"] = "feasible",
    wait: int | None = None,
    pickup: int | None = None,
    driving: int | None = None,
) -> TransportCandidate:
    return TransportCandidate(
        mode=mode,
        door_to_door_minutes=duration,
        walking_minutes=duration if mode == "walk" and duration is not None else 0,
        distance_meters=500,
        cost=CostRange(min_krw=minimum, max_krw=maximum, is_estimated=mode == "taxi"),
        status=status,
        wait_minutes=wait,
        pickup_buffer_minutes=pickup,
        driving_minutes=driving,
        evidence_fact_ids=(f"fact-{mode}",),
    )


def test_walk_within_fifteen_minutes_wins_over_taxi() -> None:
    """직접 도보가 15분 이내면 staging에서도 택시보다 먼저 선택해야 한다."""

    result = select_cost_time_balance(
        walk=_candidate("walk", 15, 0, 0),
        bus=None,
        taxi=_candidate("taxi", 12, 7_000, 9_000, pickup=10, driving=2),
        preferences=TransportPreferences(selection_policy="cost_time_balance"),
    )

    assert result.selected.mode == "walk"
    assert "WALK_WITHIN_15_MINUTES" in result.decision.reason_codes


@pytest.mark.parametrize(
    ("bus_minutes", "bus_cost", "taxi_cost", "expected_mode", "expected_reason"),
    [
        (35, (1_200, 1_200), (7_000, 9_000), "bus", "BUS_SAVES_AT_LEAST_5000_WITHIN_30_MINUTES"),
        (61, (1_200, 1_200), (7_000, 9_000), "taxi", "TAXI_BUS_TIME_PENALTY_EXCEEDS_30_MINUTES"),
        (35, (1_200, 1_200), (5_000, 6_000), "taxi", "TAXI_BUS_SAVINGS_BELOW_5000_KRW"),
    ],
    ids=("오천원 절약 경계", "삼십분 초과", "절약액 부족"),
)
def test_bus_taxi_boundary_reasons_are_preserved(
    bus_minutes: int,
    bus_cost: tuple[int, int],
    taxi_cost: tuple[int, int],
    expected_mode: str,
    expected_reason: str,
) -> None:
    """버스 절약액과 추가시간 경계는 선택 결과와 이유 코드에 함께 남아야 한다."""

    result = select_cost_time_balance(
        walk=_candidate("walk", 45, 0, 0),
        bus=_candidate("bus", bus_minutes, *bus_cost, wait=8),
        taxi=_candidate("taxi", 30, *taxi_cost, pickup=10, driving=20),
        preferences=TransportPreferences(selection_policy="cost_time_balance"),
    )

    assert result.selected.mode == expected_mode
    assert expected_reason in result.decision.reason_codes
    assert result.decision.cost_midpoint_savings_krw == (sum(taxi_cost) // 2 - sum(bus_cost) // 2)
    assert result.decision.bus_extra_minutes == bus_minutes - 30


def test_unverified_bus_pattern_is_kept_as_an_alternative() -> None:
    """노선 후보에 정확한 시각이 없으면 버스 없음이 아니라 검증 불가 대안으로 보존해야 한다."""

    result = select_cost_time_balance(
        walk=_candidate("walk", 40, 0, 0),
        bus=_candidate("bus", None, 1_200, 1_200, status="unverifiable"),
        taxi=_candidate("taxi", 20, 7_000, 9_000, pickup=10, driving=10),
        preferences=TransportPreferences(selection_policy="cost_time_balance"),
    )

    assert result.selected.mode == "taxi"
    bus = next(item for item in result.alternatives if item.mode == "bus")
    assert bus.status == "unverifiable"
    assert "BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED" in bus.reason_codes
    assert result.decision.taxi_pickup_buffer_minutes == 10
    assert result.decision.taxi_driving_minutes == 10
    assert result.decision.taxi_door_to_door_minutes == 20


def test_bus_selection_near_threshold_is_explicit() -> None:
    """버스 시간·절약액이 선택 문턱에 가까우면 재실행 변동 가능성을 명시해야 한다."""

    result = select_cost_time_balance(
        walk=_candidate("walk", 70, 0, 0),
        bus=_candidate("bus", 58, 1_200, 1_200, wait=12),
        taxi=_candidate("taxi", 30, 7_000, 9_000, pickup=10, driving=20),
        preferences=TransportPreferences(selection_policy="cost_time_balance"),
    )

    assert result.selected.mode == "bus"
    assert result.decision.selection_near_threshold is True
    assert result.decision.bus_time_threshold_margin_minutes == 2
    assert result.decision.bus_savings_threshold_margin_krw == 1_800
    assert "BUS_SELECTION_NEAR_THRESHOLD" in result.decision.reason_codes
