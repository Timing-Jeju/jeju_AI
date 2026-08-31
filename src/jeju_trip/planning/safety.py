"""도보와 승차 안전시간 계산."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class WalkSafetyResult:
    expected_minutes: int
    speed_multiplier: float
    route_uncertainty_minutes: int
    planned_minutes: int


@dataclass(frozen=True)
class BoardingSafetyResult:
    recommended_stop_arrival_at: datetime
    latest_safe_origin_departure_at: datetime


def calculate_planned_walk(
    expected_minutes: int, speed_multiplier: float, route_uncertainty_minutes: int
) -> WalkSafetyResult:
    if expected_minutes < 0 or speed_multiplier < 1 or route_uncertainty_minutes < 0:
        raise ValueError("INVALID_WALK_SAFETY_INPUT")
    planned = math.ceil(expected_minutes * speed_multiplier) + route_uncertainty_minutes
    return WalkSafetyResult(
        expected_minutes=expected_minutes,
        speed_multiplier=speed_multiplier,
        route_uncertainty_minutes=route_uncertainty_minutes,
        planned_minutes=planned,
    )


def calculate_boarding_safety(
    scheduled_departure_at: datetime,
    planned_access_walk_minutes: int,
    boarding_buffer_minutes: int,
    departure_preparation_minutes: int,
) -> BoardingSafetyResult:
    if min(planned_access_walk_minutes, boarding_buffer_minutes, departure_preparation_minutes) < 0:
        raise ValueError("INVALID_BOARDING_SAFETY_INPUT")
    recommended = scheduled_departure_at - timedelta(minutes=boarding_buffer_minutes)
    latest_origin = recommended - timedelta(
        minutes=planned_access_walk_minutes + departure_preparation_minutes
    )
    return BoardingSafetyResult(recommended, latest_origin)
