"""버스·도보 연결 계약 테스트."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from jeju_trip.domain.models import BusRide, Coordinates, Transfer, WalkConnection
from tests.factories import KST


def _walk(kind: str) -> dict[str, object]:
    return {
        "kind": kind,
        "from_id": "entrance-1",
        "to_id": "stop-1",
        "distance_meters": 500,
        "expected_minutes": 7,
        "speed_multiplier": 1.15,
        "route_uncertainty_minutes": 2,
        "planned_minutes": 11,
        "entrance_verification": "VERIFIED",
        "evidence_fact_ids": ["fact-walk"],
    }


def _ride() -> BusRide:
    departure = datetime(2026, 8, 15, 10, tzinfo=KST)
    return BusRide(
        canonical_boarding_stop_id="canonical-1",
        provider_boarding_stop_id="provider-1",
        boarding_stop_name="공항 정류장",
        boarding_stop_position=Coordinates(latitude=33.51, longitude=126.49),
        boarding_direction="함덕 방면",
        canonical_alighting_stop_id="canonical-2",
        provider_alighting_stop_id="provider-2",
        alighting_stop_name="함덕 정류장",
        alighting_stop_position=Coordinates(latitude=33.54, longitude=126.67),
        route_id="route-101",
        route_number="101",
        scheduled_departure_at=departure,
        scheduled_arrival_at=departure + timedelta(minutes=50),
        recommended_stop_arrival_at=departure - timedelta(minutes=7),
        boarding_buffer_minutes=7,
        mapping_status="CONFIRMED",
        evidence_fact_ids=("fact-bus",),
    )


def test_bus_transfer_requires_access_and_egress_walks() -> None:
    """버스 이동에는 승차 전 도보와 하차 후 도보가 모두 있어야 한다."""

    with pytest.raises(ValidationError, match="egress_walk"):
        Transfer(
            mode="bus",
            access_walk=WalkConnection.model_validate(_walk("access_walk")),
            bus_rides=(_ride(),),
        )


def test_place_representative_point_is_preserved_as_provisional_endpoint() -> None:
    """장소 대표 좌표 보행은 검증 입구로 가장하지 않고 임시 endpoint로 보존해야 한다."""

    payload = _walk("access_walk")
    payload["entrance_verification"] = "PROVISIONAL_PLACE_POINT"
    connection = WalkConnection.model_validate(payload)
    assert connection.entrance_verification == "PROVISIONAL_PLACE_POINT"


def test_unknown_endpoint_verification_value_is_rejected() -> None:
    """정의하지 않은 endpoint 검증값은 느슨한 대표좌표 정책에서도 거부해야 한다."""

    payload = _walk("access_walk")
    payload["entrance_verification"] = "UNVERIFIED"
    with pytest.raises(ValidationError):
        WalkConnection.model_validate(payload)


def test_scheduler_rejects_bus_departure_without_boarding_buffer() -> None:
    """정류장 도착 안전시간을 확보하지 못한 버스 탑승은 일정에서 제외해야 한다."""

    payload = _ride().model_dump()
    payload["recommended_stop_arrival_at"] = payload["scheduled_departure_at"] - timedelta(
        minutes=2
    )
    with pytest.raises(ValidationError, match="boarding buffer"):
        BusRide.model_validate(payload)


def test_bus_schedule_rejects_non_korean_timezone() -> None:
    """공식 시간표 버스 시각은 UTC가 아니라 공개 계약의 한국 시간대로 정규화해야 한다."""

    payload = _ride().model_dump()
    departure = payload["scheduled_departure_at"].astimezone(UTC)
    payload["scheduled_departure_at"] = departure
    payload["scheduled_arrival_at"] = departure + timedelta(minutes=50)
    payload["recommended_stop_arrival_at"] = departure - timedelta(minutes=7)

    with pytest.raises(ValidationError, match=r"\+09:00"):
        BusRide.model_validate(payload)
