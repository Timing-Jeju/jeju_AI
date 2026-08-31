"""TAGO 실시간 도착예측의 판정 근거 결합 테스트."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jeju_trip.domain.models import (
    AccommodationInput,
    ActivityWindow,
    FullTimelineActivity,
    FullTimelineEvaluationInput,
    FullTimelineTransfer,
    PlaceReference,
    ProgressInput,
    RevalidateJejuDayTripInput,
)
from jeju_trip.infrastructure.public_data_normalizers import TagoBusArrivalRecord
from jeju_trip.infrastructure.realtime_bus_adapter import RealtimeArrivalSnapshot
from jeju_trip.infrastructure.realtime_evidence import TagoRealtimeEvaluationEvidence
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.planning.evaluation import RouteEvidence
from jeju_trip.planning.execution_budget import ExecutionBudget
from tests.planning.test_evaluation_engine import FixedEvidence, _request

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[2]


class RealtimeReadyEvidence(FixedEvidence):
    """정적 대기시간과 정류장 mapping ID를 포함한 버스 근거를 제공한다."""

    def route(self, from_place_id, to_place_id, departure_at):
        base = super().route(from_place_id, to_place_id, departure_at)
        return replace(
            base,
            provider_route_id="route-101",
            boarding_stop_id="canonical-stop-1",
            scheduled_wait_minutes=10,
        )


class CurrentRouteChangedEvidence(FixedEvidence):
    """현재시각 최적편과 계획시각에 검증된 선택편이 서로 다른 근거를 제공한다."""

    def route(self, from_place_id, to_place_id, departure_at):
        return self.route_for_mode(from_place_id, to_place_id, departure_at, "bus")

    def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
        base = super().route(from_place_id, to_place_id, departure_at)
        planned = departure_at == datetime(2026, 8, 15, 11, tzinfo=KST)
        return replace(
            base,
            provider_route_id="route-101" if planned else "route-202",
            route_number="101" if planned else "202",
            boarding_stop_id="canonical-stop-1" if planned else "canonical-stop-2",
            alighting_stop_id="canonical-stop-3",
            scheduled_departure_at=datetime(2026, 8, 15, 11, 5, tzinfo=KST),
            scheduled_arrival_at=datetime(2026, 8, 15, 11, 40, tzinfo=KST),
            scheduled_wait_minutes=10,
        )


class FixedArrivalAdapter:
    """공식 adapter 대신 20분 뒤 도착하는 typed snapshot을 제공한다."""

    def fetch(self, contract, *, city_code, node_id, environment):
        checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
        arrival = TagoBusArrivalRecord(
            fact_id="fact-arrival-101",
            city_code="39",
            provider_stop_id="provider-stop-1",
            provider_route_id="route-101",
            route_number="101",
            arrival_seconds=1200,
            remaining_stops=5,
            checked_at=checked_at,
            expected_arrival_at=checked_at + timedelta(minutes=20),
        )
        return RealtimeArrivalSnapshot(checked_at, (arrival,))


class FixedResolver:
    """canonical 정류장을 확인된 TAGO 정류장으로 연결한다."""

    def resolve_stop(self, canonical_stop_id):
        return "39", "provider-stop-1", "CONFIRMED"


def test_realtime_wait_replaces_static_wait_without_double_counting() -> None:
    """실시간 대기시간은 정적 대기시간을 먼저 빼고 교체해 중복 합산하지 않아야 한다."""

    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    request = RevalidateJejuDayTripInput(
        checked_at=checked_at,
        progress=ProgressInput(
            state="waiting_bus",
            current_event_id="transfer-1",
            actual_time=checked_at,
            current_stop_id="canonical-stop-1",
            current_route_id="route-101",
        ),
        itinerary=_request(12),
    )
    evidence = TagoRealtimeEvaluationEvidence(
        RealtimeReadyEvidence(),
        FixedArrivalAdapter(),  # type: ignore[arg-type]
        SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-arrival"),
        FixedResolver(),
        {"JEJU_TAGO_SERVICE_KEY": "secret"},
        request,
        ExecutionBudget.realtime(),
    )
    route = evidence.route("place-1", "place-2", checked_at)
    assert isinstance(route, RouteEvidence)
    assert route.duration_minutes == 50
    assert route.evidence_fact_ids[-1] == "fact-arrival-101"


def test_realtime_rechecks_selected_route_at_its_planned_time() -> None:
    """현재 최적편이 달라도 계획시각에 검증된 선택 노선·정류장에는 TAGO를 결합해야 한다."""

    planned_at = datetime(2026, 8, 15, 11, tzinfo=KST)
    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    itinerary = FullTimelineEvaluationInput(
        trip_date=planned_at.date(),
        accommodation=AccommodationInput(place_id="hotel", name="숙소"),
        activity_window=ActivityWindow(
            start_at=planned_at,
            end_at=planned_at + timedelta(hours=2),
        ),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="selected-bus",
                type="transfer",
                start_at=planned_at,
                end_at=planned_at + timedelta(minutes=50),
                from_place=PlaceReference(place_id="hotel"),
                to_place=PlaceReference(place_id="place-1"),
                planned_mode="bus",
                planned_route_number="101",
                planned_route_id="route-101",
                planned_boarding_stop_id="canonical-stop-1",
                planned_alighting_stop_id="canonical-stop-3",
                scheduled_departure_at=planned_at + timedelta(minutes=5),
                scheduled_arrival_at=planned_at + timedelta(minutes=40),
            ),
            FullTimelineActivity(
                event_id="visit-1",
                type="visit",
                start_at=planned_at + timedelta(minutes=50),
                end_at=planned_at + timedelta(minutes=110),
                place=PlaceReference(place_id="place-1"),
            ),
        ),
    )
    request = RevalidateJejuDayTripInput(
        checked_at=checked_at,
        progress=ProgressInput(
            state="waiting_bus",
            current_event_id="selected-bus",
            actual_time=checked_at,
            current_place_id="hotel",
            current_stop_id="canonical-stop-1",
            current_route_id="route-101",
        ),
        itinerary=itinerary,
    )
    evidence = TagoRealtimeEvaluationEvidence(
        CurrentRouteChangedEvidence(),
        FixedArrivalAdapter(),  # type: ignore[arg-type]
        SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-arrival"),
        FixedResolver(),
        {"JEJU_TAGO_SERVICE_KEY": "secret"},
        request,
        ExecutionBudget.realtime(),
    )

    route = evidence.route("hotel", "place-1", checked_at)

    assert isinstance(route, RouteEvidence)
    assert route.provider_route_id == "route-101"
    assert route.boarding_stop_id == "canonical-stop-1"
    assert route.evidence_fact_ids[-1] == "fact-arrival-101"
