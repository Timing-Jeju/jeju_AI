"""versioned 계획 정책과 공식 제주 택시 요금 계산."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyModel(BaseModel):
    """알 수 없는 정책 필드를 거부하는 내부 정책 모델."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WalkingSpeedMultiplierPolicy(PolicyModel):
    normal: float = Field(ge=1)
    relaxed: float = Field(ge=1)
    senior_or_child: float = Field(ge=1)
    mobility_support: float = Field(ge=1)


class WalkingRoutePrefilterPolicy(PolicyModel):
    """직접 도보 문턱을 넘는 것이 확실한 구간의 보수적 호출 생략 기준."""

    maximum_planning_speed_kph: float = Field(gt=0)


class BoardingBufferPolicy(PolicyModel):
    normal: int = Field(ge=0)
    transfer: int = Field(ge=0)
    last_or_low_frequency_extra: int = Field(ge=0)
    hotel_return: int = Field(ge=0)
    route_uncertainty: int = Field(ge=0)


class TaxiPickupBufferPolicy(PolicyModel):
    """실시간 배차값이 없을 때 전략별로 적용하는 계획 안전 버퍼."""

    balanced: int = Field(ge=0)
    relaxed: int = Field(ge=0)
    experience_max: int = Field(ge=0)


class MealWindowPolicy(PolicyModel):
    lunch_start: time
    lunch_end: time
    dinner_start: time
    dinner_end: time
    default_duration_minutes: int = Field(gt=0)


class RestPolicy(PolicyModel):
    minimum_break_minutes: int = Field(gt=0)


class RiskSlackPolicy(PolicyModel):
    high_upper_exclusive: int = Field(gt=0)
    medium_upper_exclusive: int = Field(gt=0)


class StayDurationPolicy(PolicyModel):
    minimum: int = Field(gt=0)
    recommended: int = Field(gt=0)
    maximum: int = Field(gt=0)


class StrategyScoreWeights(PolicyModel):
    preferred_places: int = Field(ge=0)
    travel_efficiency: int = Field(ge=0)
    reliability: int = Field(ge=0)
    comfort: int = Field(ge=0)
    cost_efficiency: int = Field(ge=0)
    data_confidence: int = Field(ge=0)

    def model_post_init(self, context: object) -> None:
        if sum(self.model_dump().values()) != 100:
            raise ValueError("STRATEGY_SCORE_WEIGHTS_MUST_TOTAL_100")


class PlanningPolicy(PolicyModel):
    schema_version: Literal["planning-policy-v1"]
    policy_version: str
    effective_from: date
    walking_speed_multiplier: WalkingSpeedMultiplierPolicy
    walking_route_prefilter: WalkingRoutePrefilterPolicy
    boarding_buffer_minutes: BoardingBufferPolicy
    taxi_pickup_buffer_minutes: TaxiPickupBufferPolicy
    meal_windows: MealWindowPolicy
    rest: RestPolicy
    risk_slack_minutes: RiskSlackPolicy
    strategy_score_weights: dict[
        Literal["balanced", "relaxed", "experience_max"], StrategyScoreWeights
    ]
    stay_minutes: dict[str, StayDurationPolicy]
    discovery_content_types: dict[str, tuple[str, ...]]


class TaxiVehiclePolicy(PolicyModel):
    vehicle_type: Literal["SMALL", "STANDARD", "LARGE"]
    base_fare_krw: int = Field(ge=0)
    base_distance_meters: int = Field(gt=0)
    distance_unit_meters: int = Field(gt=0)
    distance_unit_fare_krw: int = Field(ge=0)
    time_speed_threshold_kph: int = Field(gt=0)
    time_unit_seconds: int = Field(gt=0)
    time_unit_fare_krw: int = Field(ge=0)
    long_distance_threshold_meters: int | None = Field(default=None, gt=0)
    long_distance_unit_fare_krw: int | None = Field(default=None, ge=0)


class TaxiFarePolicy(PolicyModel):
    schema_version: Literal["jeju-taxi-fare-v1"]
    source_id: Literal["jeju.taxi-fare-policy"]
    source_url: str
    verified_on: date
    effective_from: date
    effective_to: date | None = None
    applies_to_region: str
    night_start: time
    night_end: time
    night_surcharge_ratio: float = Field(ge=0, le=1)
    call_fee_max_krw: int = Field(ge=0)
    dispatch_guaranteed: Literal[False]
    vehicle_types: tuple[TaxiVehiclePolicy, ...]

    def model_post_init(self, context: object) -> None:
        if self.verified_on < self.effective_from:
            raise ValueError("POLICY_VERIFICATION_BEFORE_EFFECTIVE_DATE")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("POLICY_DATE_RANGE_INVALID")
        vehicle_types = tuple(item.vehicle_type for item in self.vehicle_types)
        if len(set(vehicle_types)) != len(vehicle_types):
            raise ValueError("TAXI_VEHICLE_POLICY_DUPLICATED")
        if "STANDARD" not in vehicle_types:
            raise ValueError("TAXI_STANDARD_POLICY_REQUIRED")


class BusPassengerFarePolicy(PolicyModel):
    adult_min_krw: int = Field(ge=0)
    adult_max_krw: int = Field(ge=0)
    child_min_krw: int = Field(ge=0)
    child_max_krw: int = Field(ge=0)

    def model_post_init(self, context: object) -> None:
        if self.adult_max_krw < self.adult_min_krw:
            raise ValueError("BUS_ADULT_FARE_RANGE_INVALID")
        if self.child_max_krw < self.child_min_krw:
            raise ValueError("BUS_CHILD_FARE_RANGE_INVALID")


class BusFarePolicy(PolicyModel):
    schema_version: Literal["jeju-bus-fare-v1"]
    source_id: Literal["jeju.bus-fare-policy"]
    source_url: str
    verified_on: date
    effective_from: date
    effective_to: date | None = None
    standard: BusPassengerFarePolicy
    express: BusPassengerFarePolicy

    def model_post_init(self, context: object) -> None:
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("POLICY_DATE_RANGE_INVALID")


def _require_policy_date(value: date, effective_from: date, effective_to: date | None) -> None:
    if value < effective_from or (effective_to is not None and value > effective_to):
        raise ValueError("FARE_POLICY_NOT_EFFECTIVE")


@dataclass(frozen=True)
class TaxiFareEstimate:
    minimum_krw: int
    maximum_krw: int
    is_estimated: Literal[True]
    dispatch_guaranteed: Literal[False]
    input_fact_ids: tuple[str, str]


def load_planning_policy(path: Path) -> PlanningPolicy:
    with path.open("rb") as stream:
        return PlanningPolicy.model_validate(tomllib.load(stream))


def load_taxi_fare_policy(path: Path) -> TaxiFarePolicy:
    with path.open("rb") as stream:
        return TaxiFarePolicy.model_validate(tomllib.load(stream))


def load_bus_fare_policy(path: Path) -> BusFarePolicy:
    with path.open("rb") as stream:
        return BusFarePolicy.model_validate(tomllib.load(stream))


def estimate_bus_fare(
    policy: BusFarePolicy,
    *,
    route_type: str,
    adults: int,
    seniors: int,
    children: int,
    travel_date: date,
) -> tuple[int, int]:
    """주민 할인·카드 보유를 가정하지 않고 공식 표의 범위로 계산한다."""

    _require_policy_date(travel_date, policy.effective_from, policy.effective_to)
    fare = policy.express if "급행" in route_type else policy.standard
    adult_count = adults + seniors
    minimum = adult_count * fare.adult_min_krw + children * fare.child_min_krw
    maximum = adult_count * fare.adult_max_krw + children * fare.child_max_krw
    return minimum, maximum


def _ceil_units(value: int, unit: int) -> int:
    return 0 if value <= 0 else (value + unit - 1) // unit


def _is_night(value: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= value < end
    return value >= start or value < end


def estimate_taxi_fare(
    policy: TaxiFarePolicy,
    *,
    vehicle_type: Literal["SMALL", "STANDARD", "LARGE"],
    distance_meters: int,
    duration_seconds: int,
    departure_at: datetime,
    route_fact_id: str,
    policy_fact_id: str,
) -> TaxiFareEstimate:
    """공식 요율과 경로 fact로 확정값이 아닌 보수적 요금 범위를 계산한다."""

    if distance_meters < 0 or duration_seconds < 0:
        raise ValueError("TAXI_ROUTE_VALUE_NEGATIVE")
    _require_policy_date(
        departure_at.date(),
        policy.effective_from,
        policy.effective_to,
    )
    vehicle = next(
        (item for item in policy.vehicle_types if item.vehicle_type == vehicle_type),
        None,
    )
    if vehicle is None:
        raise ValueError("TAXI_VEHICLE_POLICY_MISSING")

    distance_unit_fare = vehicle.distance_unit_fare_krw
    if (
        vehicle.long_distance_threshold_meters is not None
        and distance_meters >= vehicle.long_distance_threshold_meters
        and vehicle.long_distance_unit_fare_krw is not None
    ):
        distance_unit_fare = vehicle.long_distance_unit_fare_krw
    excess_distance = max(0, distance_meters - vehicle.base_distance_meters)
    distance_fare = (
        vehicle.base_fare_krw
        + _ceil_units(excess_distance, vehicle.distance_unit_meters) * distance_unit_fare
    )
    slow_time_upper = (
        _ceil_units(duration_seconds, vehicle.time_unit_seconds) * vehicle.time_unit_fare_krw
    )
    minimum = distance_fare
    maximum = distance_fare + slow_time_upper + policy.call_fee_max_krw
    if _is_night(departure_at.timetz().replace(tzinfo=None), policy.night_start, policy.night_end):
        minimum = math.ceil(minimum * (1 + policy.night_surcharge_ratio))
        maximum = math.ceil(maximum * (1 + policy.night_surcharge_ratio))
    return TaxiFareEstimate(
        minimum_krw=minimum,
        maximum_krw=maximum,
        is_estimated=True,
        dispatch_guaranteed=False,
        input_fact_ids=(route_fact_id, policy_fact_id),
    )
