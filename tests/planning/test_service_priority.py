"""v0.5 서비스 우선 이동수단 정책을 검증한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jeju_trip.planning.service_priority import ModeCandidate, select_service_priority_mode

KST = timezone(timedelta(hours=9))


def test_bus_departure_after_thirty_minutes_is_rejected() -> None:
    """입구 접근과 7분 승차 준비를 마친 뒤 31분 대기하는 버스는 선택하지 않아야 한다."""

    departure = datetime(2026, 8, 15, 10, tzinfo=KST)
    result = select_service_priority_mode(
        departure_at=departure,
        walk=None,
        bus=ModeCandidate(
            mode="bus",
            duration_minutes=60,
            distance_meters=18_000,
            access_walk_minutes=5,
            scheduled_departure_at=departure + timedelta(minutes=43),
            arrival_at=departure + timedelta(minutes=90),
            evidence_fact_ids=("fact-trip",),
        ),
        taxi=ModeCandidate(
            mode="taxi",
            duration_minutes=25,
            distance_meters=18_000,
            arrival_at=departure + timedelta(minutes=25),
            cost_max_krw=25_000,
            evidence_fact_ids=("fact-taxi",),
        ),
        deadline=departure + timedelta(hours=2),
        max_walk_minutes=15,
    )

    assert result.selected_mode == "taxi"
    assert "BUS_NO_DEPARTURE_WITHIN_30_MINUTES" in result.reason_codes


def test_unverified_bus_does_not_fall_back_to_taxi() -> None:
    """버스 서비스데이 또는 mapping이 불명확하면 택시로 우회해 완성하지 않아야 한다."""

    departure = datetime(2026, 8, 15, 10, tzinfo=KST)
    result = select_service_priority_mode(
        departure_at=departure,
        walk=None,
        bus=ModeCandidate(
            mode="bus",
            duration_minutes=60,
            distance_meters=18_000,
            verified=False,
            reason_codes=("BUS_SERVICE_DAY_UNVERIFIED",),
        ),
        taxi=ModeCandidate(
            mode="taxi",
            duration_minutes=25,
            distance_meters=18_000,
            arrival_at=departure + timedelta(minutes=25),
            cost_max_krw=25_000,
            evidence_fact_ids=("fact-taxi",),
        ),
        deadline=departure + timedelta(hours=2),
        max_walk_minutes=15,
    )

    assert result.selected_mode is None
    assert result.blocking is True
    assert result.reason_codes == ("BUS_SERVICE_DAY_UNVERIFIED",)
