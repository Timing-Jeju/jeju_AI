"""실시간 남은 일정 재판정 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jeju_trip.application.service import RealtimeEvidencePreparation, TripPlannerService
from jeju_trip.domain.models import (
    AccommodationInput,
    ActivityWindow,
    FullTimelineActivity,
    FullTimelineEvaluationInput,
    FullTimelineTransfer,
    LegConstraint,
    PlaceReference,
    ProgressInput,
    RevalidateJejuDayTripInput,
)
from jeju_trip.planning.execution_budget import ExecutionBudget, PlanningTimeout
from tests.planning.test_evaluation_engine import FixedEvidence, _request

KST = timezone(timedelta(hours=9))


class FixedRealtimeProvider:
    """실시간 테스트에서 갱신된 근거가 준비된 상태를 제공한다."""

    def prepare(self, request, budget):
        return RealtimeEvidencePreparation(FixedEvidence())


def test_remaining_full_timeline_preserves_selected_modes_between_activities() -> None:
    """전체 타임라인의 남은 일정 변환은 이미 선택한 구간 수단을 잃지 않아야 한다."""

    start = datetime(2026, 8, 15, 9, tzinfo=KST)
    itinerary = FullTimelineEvaluationInput(
        trip_date=start.date(),
        accommodation=AccommodationInput(place_id="hotel", name="숙소"),
        activity_window=ActivityWindow(start_at=start, end_at=start + timedelta(hours=4)),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="outbound",
                type="transfer",
                start_at=start,
                end_at=start + timedelta(minutes=20),
                from_place=PlaceReference(place_id="hotel"),
                to_place=PlaceReference(place_id="place-1"),
                planned_mode="taxi",
            ),
            FullTimelineActivity(
                event_id="visit-1",
                type="visit",
                start_at=start + timedelta(minutes=20),
                end_at=start + timedelta(minutes=80),
                place=PlaceReference(place_id="place-1"),
            ),
            FullTimelineTransfer(
                event_id="middle",
                type="transfer",
                start_at=start + timedelta(minutes=80),
                end_at=start + timedelta(minutes=95),
                from_place=PlaceReference(place_id="place-1"),
                to_place=PlaceReference(place_id="place-2"),
                planned_mode="walk",
            ),
            FullTimelineActivity(
                event_id="visit-2",
                type="visit",
                start_at=start + timedelta(minutes=95),
                end_at=start + timedelta(minutes=155),
                place=PlaceReference(place_id="place-2"),
            ),
            FullTimelineTransfer(
                event_id="return",
                type="transfer",
                start_at=start + timedelta(minutes=155),
                end_at=start + timedelta(minutes=175),
                from_place=PlaceReference(place_id="place-2"),
                to_place=PlaceReference(place_id="hotel"),
                planned_mode="taxi",
            ),
        ),
    )
    request = RevalidateJejuDayTripInput(
        checked_at=start + timedelta(minutes=20),
        progress=ProgressInput(
            state="at_place",
            current_event_id="visit-1",
            actual_time=start + timedelta(minutes=20),
            current_place_id="place-1",
            current_event_started_at=start + timedelta(minutes=20),
        ),
        itinerary=itinerary,
    )

    remaining = TripPlannerService._remaining_request(request)

    assert remaining is not None
    assert remaining.start_location is not None
    assert remaining.start_location.place_id == "place-1"
    assert remaining.accommodation.place_id == "hotel"
    constraints = {
        (item.from_event_id, item.to_event_id): item.locked_mode
        for item in remaining.leg_constraints
    }
    assert constraints[("visit-1", "visit-2")] == "walk"
    assert constraints[("visit-2", None)] == "taxi"


def test_waiting_bus_preserves_current_transfer_mode_to_next_activity() -> None:
    """버스 대기 재판정은 현재 선택된 첫 버스 구간을 남은 일정의 고정 수단으로 보존해야 한다."""

    start = datetime(2026, 8, 24, 9, 30, tzinfo=KST)
    itinerary = FullTimelineEvaluationInput(
        trip_date=start.date(),
        accommodation=AccommodationInput(place_id="hotel", name="숙소"),
        activity_window=ActivityWindow(start_at=start, end_at=start + timedelta(hours=3)),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="selected-bus",
                type="transfer",
                start_at=start,
                end_at=start + timedelta(minutes=30),
                from_place=PlaceReference(place_id="hotel"),
                to_place=PlaceReference(place_id="place-1"),
                planned_mode="bus",
                planned_route_number="201",
                planned_route_id="route-201",
                planned_boarding_stop_id="stop-hotel",
                planned_alighting_stop_id="stop-place-1",
                scheduled_departure_at=start + timedelta(minutes=5),
                scheduled_arrival_at=start + timedelta(minutes=25),
            ),
            FullTimelineActivity(
                event_id="visit-1",
                type="visit",
                start_at=start + timedelta(minutes=30),
                end_at=start + timedelta(minutes=90),
                place=PlaceReference(place_id="place-1"),
            ),
        ),
    )
    request = RevalidateJejuDayTripInput(
        checked_at=start,
        progress=ProgressInput(
            state="waiting_bus",
            current_event_id="selected-bus",
            actual_time=start,
            current_place_id="hotel",
            current_stop_id="stop-hotel",
            current_route_id="route-201",
        ),
        itinerary=itinerary,
    )

    remaining = TripPlannerService._remaining_request(request)

    assert remaining is not None
    assert remaining.leg_constraints == (
        LegConstraint(from_event_id=None, to_event_id="visit-1", locked_mode="bus"),
    )


def test_revalidation_calculates_delay_and_excludes_completed_current_activity() -> None:
    """실시간 재판정은 실제 지연을 계산하고 출발 완료한 현재 활동을 남은 일정에서 제외해야 한다."""

    itinerary = _request(12)
    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    response = TripPlannerService(
        evaluation_evidence=FixedEvidence(),
        realtime_evidence_provider=FixedRealtimeProvider(),
    ).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="ready_to_depart",
                current_event_id="visit-1",
                completed_event_ids=(),
                actual_time=checked_at,
                current_place_id="place-1",
            ),
            itinerary=itinerary,
        )
    )
    assert response.delay_minutes == 15
    assert response.status == "at_risk"
    assert response.remaining_evaluation is not None
    remaining_ids = {
        event.source_event_id for event in response.remaining_evaluation.normalized_schedule.events
    }
    assert "visit-1" not in remaining_ids
    assert "visit-2" in remaining_ids


def test_moving_without_location_context_disables_exact_rerouting() -> None:
    """버스 이동 중 정류장·노선 정보가 없으면 입력 계약에서 거부해야 한다."""

    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    with pytest.raises(ValidationError):
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="on_bus",
                current_event_id="visit-1",
                completed_event_ids=(),
                actual_time=checked_at,
            ),
            itinerary=_request(12),
        )


def test_bus_revalidation_without_realtime_provider_never_claims_on_schedule() -> None:
    """버스 구간이 남았는데 실시간 provider가 없으면 정적 근거만으로 정상 판정하지 않아야 한다."""

    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    response = TripPlannerService(evaluation_evidence=FixedEvidence()).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="ready_to_depart",
                current_event_id="visit-1",
                actual_time=checked_at,
                current_place_id="place-1",
            ),
            itinerary=_request(12),
        )
    )
    assert response.status == "at_risk"
    assert response.remaining_evaluation is not None
    assert "REALTIME_PROVIDER_UNAVAILABLE" not in response.warnings


def test_at_place_remaining_stay_uses_actual_activity_start() -> None:
    """활동 중 남은 체류시간은 과거 계획 시작이 아니라 실제 시작과 확인시각으로 계산해야 한다."""

    checked_at = datetime(2026, 8, 15, 10, 20, tzinfo=KST)
    request = RevalidateJejuDayTripInput(
        checked_at=checked_at,
        progress=ProgressInput(
            state="at_place",
            current_event_id="visit-1",
            actual_time=checked_at,
            current_place_id="place-1",
            current_event_started_at=datetime(2026, 8, 15, 10, 10, tzinfo=KST),
        ),
        itinerary=_request(12),
    )

    remaining = TripPlannerService._remaining_request(request)

    assert remaining is not None
    current = remaining.scheduled_activities[0]
    assert current.start_at == checked_at
    assert current.end_at == datetime(2026, 8, 15, 11, 10, tzinfo=KST)


def test_partial_opening_evidence_does_not_make_revalidation_at_risk() -> None:
    """운영시간만 미확인인 남은 일정은 시간 정상과 부분 근거 상태를 분리해야 한다."""

    class UnknownOpeningEvidence:
        """이동시간은 검증하되 운영시간만 제공하지 않는다."""

        def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
            return FixedEvidence().route(from_place_id, to_place_id, departure_at)

        def opening_window(self, place_id: str, on_date):
            return None

        evidence_facts = FixedEvidence.evidence_facts
        data_sources = FixedEvidence.data_sources

    checked_at = datetime(2026, 8, 15, 10, tzinfo=KST)
    response = TripPlannerService(evaluation_evidence=UnknownOpeningEvidence()).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="at_place",
                current_event_id="visit-1",
                actual_time=checked_at,
                current_place_id="place-1",
                current_event_started_at=checked_at,
            ),
            itinerary=_request(12),
        )
    )

    assert response.status == "on_schedule"
    assert response.timing_status == "on_schedule"
    assert response.evidence_status == "partial"


def test_on_bus_progress_is_partial_without_position_inference() -> None:
    """버스 탑승 중에는 위치를 임의 추정하지 않고 시간 상태와 부분 근거를 분리해야 한다."""

    checked_at = datetime(2026, 8, 15, 10, 30, tzinfo=KST)
    response = TripPlannerService(evaluation_evidence=FixedEvidence()).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="on_bus",
                current_event_id="transfer-1",
                actual_time=checked_at,
                current_stop_id="stop-a",
                current_route_id="route-201",
            ),
            itinerary=_request(12),
        )
    )

    assert response.evidence_status == "partial"
    assert response.timing_status in {"on_schedule", "at_risk"}
    assert "ON_BUS_PROGRESS_UNSUPPORTED" in response.warnings
    assert response.remaining_evaluation is None


def test_remaining_revalidation_timeout_returns_structured_unavailable(monkeypatch) -> None:
    """원 일정 평가 뒤 남은 일정이 timeout이면 예외 대신 구조화된 검증 불가를 반환해야 한다."""

    itinerary = _request(12)
    original = TripPlannerService(evaluation_evidence=FixedEvidence()).evaluate(itinerary)
    calls = 0

    def evaluate_once_then_timeout(self, request, budget=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original
        raise PlanningTimeout("PLANNING_TIMEOUT")

    monkeypatch.setattr(
        "jeju_trip.application.service.ItineraryEvaluationEngine.evaluate",
        evaluate_once_then_timeout,
    )
    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    response = TripPlannerService(
        evaluation_evidence=FixedEvidence(),
        realtime_evidence_provider=FixedRealtimeProvider(),
    ).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="ready_to_depart",
                current_event_id="visit-1",
                actual_time=checked_at,
                current_place_id="place-1",
            ),
            itinerary=itinerary,
        )
    )

    assert response.status == "data_unavailable"
    assert response.remaining_evaluation is None
    assert "PLANNING_TIMEOUT" in response.warnings


def test_revalidation_keeps_original_evaluation_calls_out_of_realtime_budget(
    monkeypatch,
) -> None:
    """원 일정 재평가가 실시간 12회 예산을 먼저 소진해 TAGO·남은 일정 조회를 막지 않아야 한다."""

    itinerary = _request(12)
    original = TripPlannerService(evaluation_evidence=FixedEvidence()).evaluate(itinerary)
    budgets: list[ExecutionBudget] = []

    def spend_original_budget_then_evaluate_remaining(self, request, budget=None):
        assert budget is not None
        budgets.append(budget)
        if len(budgets) == 1:
            for _ in range(12):
                budget.claim_external_call("original-routing")
        else:
            budget.claim_external_call("remaining-routing")
        return original

    monkeypatch.setattr(
        "jeju_trip.application.service.ItineraryEvaluationEngine.evaluate",
        spend_original_budget_then_evaluate_remaining,
    )
    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    response = TripPlannerService(
        evaluation_evidence=FixedEvidence(),
        realtime_evidence_provider=FixedRealtimeProvider(),
    ).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="waiting_bus",
                current_event_id="transfer-1",
                actual_time=checked_at,
                current_place_id="place-1",
                current_stop_id="stop-a",
                current_route_id="route-201",
            ),
            itinerary=itinerary,
        )
    )

    assert response.status != "data_unavailable"
    assert len(budgets) == 2
    assert budgets[0] is not budgets[1]


def test_revalidation_builds_fresh_evidence_for_remaining_schedule() -> None:
    """원 일정 조회가 쓴 내부 경로 예산을 남은 일정·TAGO 근거 객체에 누적하지 않아야 한다."""

    class CapturingRealtimeProvider:
        """실시간 provider가 감싼 남은 일정 근거 객체를 기록한다."""

        def __init__(self) -> None:
            self.base = None

        def prepare(self, request, budget):
            return RealtimeEvidencePreparation(FixedEvidence())

        def prepare_with_base(self, request, budget, base):
            self.base = base
            return RealtimeEvidencePreparation(base)

    factory_results = []

    def evidence_factory(request):
        evidence = FixedEvidence()
        factory_results.append(evidence)
        return evidence

    provider = CapturingRealtimeProvider()
    checked_at = datetime(2026, 8, 15, 11, 15, tzinfo=KST)
    response = TripPlannerService(
        evaluation_evidence_factory=evidence_factory,
        realtime_evidence_provider=provider,
    ).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=checked_at,
            progress=ProgressInput(
                state="waiting_bus",
                current_event_id="transfer-1",
                actual_time=checked_at,
                current_place_id="place-1",
                current_stop_id="stop-a",
                current_route_id="route-201",
            ),
            itinerary=_request(12),
        )
    )

    assert response.status != "data_unavailable"
    assert len(factory_results) == 2
    assert provider.base is factory_results[1]
