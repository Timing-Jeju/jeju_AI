"""v0.5 공개 계약의 fail-closed 불변조건을 검증한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jeju_trip.domain.models import (
    EndpointBasis,
    MealDetails,
    ModeDecision,
    ProgressInput,
    RecommendDayTripsInput,
)
from tests.factories import make_request

KST = timezone(timedelta(hours=9))


def test_v04_input_is_rejected_by_v05_contract() -> None:
    """v0.4 입력은 v0.5 공개 계약에서 조용히 호환하지 않고 거부해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["schema_version"] = "0.4.0"
    with pytest.raises(ValidationError):
        RecommendDayTripsInput.model_validate(payload)


def test_cost_time_balance_is_the_v06_default() -> None:
    """v0.6 이동수단 선택 기본 정책은 비용·시간 비교 단일 정책이어야 한다."""

    assert make_request().transport.selection_policy == "cost_time_balance"


def test_bus_access_walk_default_is_twenty_minutes() -> None:
    """동부 PoC 버스 접근 보행은 직접 도보 기준과 분리해 기본 20분까지 허용해야 한다."""

    assert make_request().walking.max_access_walk_minutes == 20


def test_taxi_specific_budget_is_rejected() -> None:
    """택시비는 표시만 하며 택시 전용 상한 입력은 공개 계약에서 거부해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["transport"]["taxi_budget_krw"] = 50_000
    with pytest.raises(ValidationError):
        RecommendDayTripsInput.model_validate(payload)


def test_meal_contract_rejects_out_of_scope_price() -> None:
    """식사 장소는 운영시간만 검증하고 식사 가격 필드를 공개 계약에 받지 않아야 한다."""

    with pytest.raises(ValidationError):
        MealDetails.model_validate(
            {
                "place_id": "meal-1",
                "venue_name": "검증 식당",
                "estimated_cost": {
                    "min_krw": 10_000,
                    "max_krw": 12_000,
                    "is_estimated": True,
                },
                "evidence_fact_ids": ["fact-hours-meal-1"],
            }
        )


def test_food_preferences_reject_meal_budget() -> None:
    """식사 가격을 다루지 않으므로 식사 예산 입력도 공개 계약에서 거부해야 한다."""

    payload = make_request().model_dump(mode="python")
    payload["food"]["meal_budget_krw"] = 20_000

    with pytest.raises(ValidationError):
        RecommendDayTripsInput.model_validate(payload)


def test_waiting_bus_progress_requires_stop_and_route() -> None:
    """버스를 기다리는 진행 상태에는 정류장과 노선 식별자가 모두 필요하다."""

    checked_at = datetime(2026, 8, 15, 10, tzinfo=KST)
    with pytest.raises(ValidationError):
        ProgressInput(
            state="waiting_bus",
            current_event_id="transfer-1",
            actual_time=checked_at,
        )


def test_mode_decision_rejects_current_gps_for_generation() -> None:
    """생성 시 이동 endpoint에는 일시적 현재 GPS를 사용할 수 없어야 한다."""

    with pytest.raises(ValidationError):
        ModeDecision(
            selected_mode="taxi",
            origin_basis=EndpointBasis.CURRENT_GPS,
            destination_basis=EndpointBasis.VERIFIED_ENTRANCE,
            walk_threshold_minutes=15,
            bus_wait_threshold_minutes=30,
            reason_codes=("TAXI_DEADLINE_RECOVERY",),
            evidence_fact_ids=("fact-route",),
        )
