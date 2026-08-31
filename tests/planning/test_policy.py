"""versioned 계획·택시 정책 loader와 계산 테스트."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from jeju_trip.planning.policy import (
    BusPassengerFarePolicy,
    TaxiFarePolicy,
    estimate_bus_fare,
    estimate_taxi_fare,
    load_bus_fare_policy,
    load_planning_policy,
    load_taxi_fare_policy,
)


def test_bus_fare_uses_official_range_without_assuming_resident_discount() -> None:
    """버스 요금은 카드 보유·도민 고령자 할인을 가정하지 않고 공식 범위를 계산해야 한다."""

    policy = load_bus_fare_policy(ROOT / "config/policies/jeju_bus_fare_2026-08-10.toml")
    minimum, maximum = estimate_bus_fare(
        policy,
        route_type="급행버스",
        adults=1,
        seniors=1,
        children=1,
        travel_date=date(2026, 8, 15),
    )
    assert minimum == 5000
    assert maximum == 7500


def test_fare_policy_rejects_trip_before_effective_date() -> None:
    """요금정책은 공식 적용 시작일보다 앞선 여행의 비용 근거로 사용하지 않아야 한다."""

    policy = load_bus_fare_policy(ROOT / "config/policies/jeju_bus_fare_2026-08-10.toml")
    with pytest.raises(ValueError, match="FARE_POLICY_NOT_EFFECTIVE"):
        estimate_bus_fare(
            policy,
            route_type="일반간선",
            adults=1,
            seniors=0,
            children=0,
            travel_date=date(2026, 8, 9),
        )


def test_bus_fare_policy_rejects_inverted_ranges() -> None:
    """버스 최소요금이 최대요금보다 크면 정책 적재 전에 거부해야 한다."""

    with pytest.raises(ValueError, match="BUS_ADULT_FARE_RANGE_INVALID"):
        BusPassengerFarePolicy(
            adult_min_krw=2000,
            adult_max_krw=1000,
            child_min_krw=0,
            child_max_krw=0,
        )


def test_taxi_policy_requires_one_standard_vehicle() -> None:
    """일정 택시 계산에 필요한 중형 정책이 없으면 정책 문서를 승인하지 않아야 한다."""

    raw = load_taxi_fare_policy(
        ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"
    ).model_dump()
    raw["vehicle_types"] = tuple(
        item for item in raw["vehicle_types"] if item["vehicle_type"] != "STANDARD"
    )

    with pytest.raises(ValueError, match="TAXI_STANDARD_POLICY_REQUIRED"):
        TaxiFarePolicy.model_validate(raw)


ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))


def test_planning_policy_loads_versioned_stay_and_buffer_values() -> None:
    """체류시간과 버퍼는 코드 상수가 아닌 versioned 정책 파일에서 읽어야 한다."""

    policy = load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml")
    assert policy.policy_version == "2026-08-11"
    assert policy.boarding_buffer_minutes.normal == 7
    assert policy.taxi_pickup_buffer_minutes.relaxed == 10
    assert policy.stay_minutes["cafe"].recommended == 30
    assert policy.discovery_content_types["museum"] == ("14",)


def test_official_standard_taxi_base_fare_is_preserved() -> None:
    """중형택시 2km 기본요금은 제주 공식 정책의 4,300원이어야 한다."""

    policy = load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml")
    estimate = estimate_taxi_fare(
        policy,
        vehicle_type="STANDARD",
        distance_meters=2000,
        duration_seconds=600,
        departure_at=datetime(2026, 8, 15, 10, tzinfo=KST),
        route_fact_id="fact-driving-1",
        policy_fact_id="fact-taxi-policy-standard-2024-07-01",
    )
    assert estimate.minimum_krw == 4300
    assert estimate.maximum_krw >= estimate.minimum_krw
    assert estimate.input_fact_ids == (
        "fact-driving-1",
        "fact-taxi-policy-standard-2024-07-01",
    )


def test_official_taxi_policy_records_latest_verification_date() -> None:
    """현행 제주 택시 요율은 공식 페이지를 다시 확인한 날짜를 정책 근거로 보존해야 한다."""

    policy = load_taxi_fare_policy(
        ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"
    )

    assert policy.verified_on == date(2026, 8, 30)


def test_night_taxi_range_applies_official_surcharge() -> None:
    """23시 이후 택시 범위에는 공식 심야 20퍼센트 할증을 적용해야 한다."""

    policy = load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml")
    daytime = estimate_taxi_fare(
        policy,
        vehicle_type="STANDARD",
        distance_meters=3000,
        duration_seconds=900,
        departure_at=datetime(2026, 8, 15, 10, tzinfo=KST),
        route_fact_id="fact-driving-day",
        policy_fact_id="fact-policy",
    )
    night = estimate_taxi_fare(
        policy,
        vehicle_type="STANDARD",
        distance_meters=3000,
        duration_seconds=900,
        departure_at=datetime(2026, 8, 15, 23, 30, tzinfo=KST),
        route_fact_id="fact-driving-night",
        policy_fact_id="fact-policy",
    )
    assert night.minimum_krw > daytime.minimum_krw
    assert night.maximum_krw > daytime.maximum_krw
