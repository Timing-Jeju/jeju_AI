"""정확 일정 공통 판정 엔진 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jeju_trip.domain.models import (
    AccommodationInput,
    ActivitiesOnlyEvaluationInput,
    ActivityWindow,
    Coordinates,
    CostRange,
    FullTimelineActivity,
    FullTimelineBuffer,
    FullTimelineEvaluationInput,
    FullTimelineTransfer,
    Party,
    PlaceReference,
    RestPreferences,
    RouteAlternativeSummary,
    ScheduledActivity,
    TransportPreferences,
    WalkingPreferences,
)
from jeju_trip.planning.evaluation import (
    EvaluationEvidence,
    ItineraryEvaluationEngine,
    RouteEvidence,
)
from tests.factories import make_data_source_metadata, make_evidence_fact

KST = timezone(timedelta(hours=9))


class FixedEvidence(EvaluationEvidence):
    """테스트 구간에 고정된 공식 근거를 제공한다."""

    def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
        return RouteEvidence(
            mode="bus",
            duration_minutes=40,
            distance_meters=12000,
            cost_krw=2400,
            walking_minutes=8,
            transfers=0,
            evidence_fact_ids=(f"fact-route-{from_place_id}-{to_place_id}",),
            route_number="201",
            provider_route_id="route-201",
            boarding_stop_id="stop-a",
            alighting_stop_id="stop-b",
            scheduled_departure_at=datetime(2026, 8, 15, 9, 10, tzinfo=KST),
            scheduled_arrival_at=datetime(2026, 8, 15, 9, 50, tzinfo=KST),
        )

    def opening_window(self, place_id: str, on_date):
        return (
            datetime(2026, 8, 15, 9, tzinfo=KST),
            datetime(2026, 8, 15, 18, tzinfo=KST),
            (f"fact-hours-{place_id}",),
        )

    def evidence_facts(self):
        """고정 adapter가 반환할 수 있는 route·운영시간 fact 본문을 함께 제공한다."""

        place_ids = (
            "hotel-1",
            "place-1",
            "place-2",
            "current-start",
            "current-place",
        )
        fact_ids = {
            *(f"fact-hours-{place_id}" for place_id in place_ids),
            *(f"fact-last-{place_id}" for place_id in place_ids),
            *(
                f"fact-{prefix}-{origin}-{destination}"
                for prefix in ("route", "taxi", "walk")
                for origin in place_ids
                for destination in place_ids
            ),
            "fact-fast-bus",
            "fact-fast-taxi",
            "fact-slow-bus",
            "fact-pickup-policy",
        }
        return tuple(make_evidence_fact(fact_id) for fact_id in sorted(fact_ids))

    def data_sources(self):
        """fixture source fact와 대응하는 공개 source metadata를 제공한다."""

        return (make_data_source_metadata(),)


class ExpensiveWalkingEvidence(FixedEvidence):
    """누적 도보와 택시 예산 검증용 경로 근거를 제공한다."""

    def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
        return RouteEvidence(
            mode="taxi",
            duration_minutes=20,
            distance_meters=10000,
            cost_krw=20000,
            walking_minutes=0,
            walking_distance_meters=0,
            transfers=0,
            evidence_fact_ids=(f"fact-taxi-{from_place_id}-{to_place_id}",),
        )


def _request(second_start_hour: int) -> ActivitiesOnlyEvaluationInput:
    return ActivitiesOnlyEvaluationInput(
        trip_date=datetime(2026, 8, 15, tzinfo=KST).date(),
        accommodation=AccommodationInput(
            place_id="hotel-1",
            name="제주 숙소",
            coordinates=Coordinates(latitude=33.489, longitude=126.498),
        ),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 20, tzinfo=KST),
        ),
        party=Party(),
        schedule_format="activities_only",
        scheduled_activities=(
            ScheduledActivity(
                event_id="visit-1",
                type="visit",
                place=PlaceReference(place_id="place-1", name="첫 장소"),
                start_at=datetime(2026, 8, 15, 10, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 11, tzinfo=KST),
            ),
            ScheduledActivity(
                event_id="visit-2",
                type="visit",
                place=PlaceReference(place_id="place-2", name="둘째 장소"),
                start_at=datetime(2026, 8, 15, second_start_hour, tzinfo=KST),
                end_at=datetime(2026, 8, 15, second_start_hour + 1, tzinfo=KST),
            ),
        ),
    )


def test_activities_only_inserts_hotel_round_trip_legs() -> None:
    """활동 전용 일정은 숙소 왕복을 포함한 모든 이동구간을 자동 생성해야 한다."""

    result = ItineraryEvaluationEngine(FixedEvidence()).evaluate(_request(12))
    assert len(result.segment_evaluations) == 3
    assert result.normalized_schedule.events[0].type == "transfer"
    assert result.normalized_schedule.events[-1].type == "transfer"
    assert result.status == "feasible"


def test_activities_only_supports_distinct_current_origin_and_hotel_return() -> None:
    """남은 일정은 현재 위치에서 출발해도 마지막 이동은 원래 숙소로 복귀해야 한다."""

    class RecordingEvidence(FixedEvidence):
        def __init__(self) -> None:
            self.routes: list[tuple[str, str]] = []

        def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
            self.routes.append((from_place_id, to_place_id))
            return super().route(from_place_id, to_place_id, departure_at)

    base = _request(12)
    request = ActivitiesOnlyEvaluationInput(
        **base.model_dump(exclude={"start_location"}),
        start_location=PlaceReference(place_id="current-place"),
    )
    evidence = RecordingEvidence()

    ItineraryEvaluationEngine(evidence).evaluate(request)

    assert evidence.routes[0] == ("current-place", "place-1")
    assert evidence.routes[-1] == ("place-2", "hotel-1")


def test_current_activity_origin_does_not_create_bus_only_mode_violation() -> None:
    """현재 장소의 첫 활동은 버스 전용 조건에서도 외부 이동이나 금지 도보를 만들지 않아야 한다."""

    class RecordingEvidence(FixedEvidence):
        def __init__(self) -> None:
            self.routes: list[tuple[str, str]] = []

        def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
            self.routes.append((from_place_id, to_place_id))
            return super().route(from_place_id, to_place_id, departure_at)

    base = _request(12)
    request = base.model_copy(
        update={
            "start_location": PlaceReference(place_id="place-1"),
            "transport": TransportPreferences(
                allowed_modes={"bus"},
                preferred_mode="bus",
                fallback_order=(),
            ),
        }
    )
    evidence = RecordingEvidence()

    result = ItineraryEvaluationEngine(evidence).evaluate(request)

    assert ("place-1", "place-1") not in evidence.routes
    current_segment = result.segment_evaluations[0]
    assert current_segment.required_minutes == 0
    assert current_segment.status == "feasible"
    assert current_segment.risk == "low"
    assert not any(issue.reason_code == "MODE_NOT_ALLOWED" for issue in result.issues)


def test_negative_transfer_slack_is_infeasible() -> None:
    """공식 이동시간보다 활동 사이 여유가 짧으면 일정은 불가능해야 한다."""

    result = ItineraryEvaluationEngine(FixedEvidence()).evaluate(_request(11))
    assert result.status == "infeasible"
    assert result.overall_risk == "critical"
    assert "NEGATIVE_TRANSFER_SLACK" in {issue.reason_code for issue in result.issues}


def test_depart_earlier_repair_is_prioritized_and_revalidated() -> None:
    """첫 활동 이동 여유 부족은 검증된 숙소 출발 앞당김 수정안을 먼저 제시해야 한다."""

    base = _request(12)
    first = base.scheduled_activities[0].model_copy(
        update={"start_at": datetime(2026, 8, 15, 9, 20, tzinfo=KST)}
    )
    request = base.model_copy(
        update={"scheduled_activities": (first, base.scheduled_activities[1])}
    )

    result = ItineraryEvaluationEngine(FixedEvidence()).evaluate(request)

    assert result.status == "infeasible"
    assert result.repair_options[0].repair_type == "DEPART_EARLIER"
    assert result.repair_options[0].result_if_applied in {
        "feasible",
        "feasible_with_caution",
    }


def test_last_admission_is_checked_separately_from_closing_time() -> None:
    """운영 중이어도 마지막 입장 이후 시작하는 활동은 불가능 판정해야 한다."""

    class LastAdmissionEvidence(FixedEvidence):
        def last_admission_at(self, place_id, on_date):
            return datetime(2026, 8, 15, 9, 50, tzinfo=KST), (f"fact-last-{place_id}",)

    result = ItineraryEvaluationEngine(LastAdmissionEvidence()).evaluate(_request(12))

    assert result.status == "infeasible"
    assert "LAST_ADMISSION_MISSED" in {issue.reason_code for issue in result.issues}


def test_full_timeline_user_route_claim_is_verified() -> None:
    """전체 일정의 버스번호와 이동시간 주장은 공식 경로와 다르면 거부해야 한다."""

    request = FullTimelineEvaluationInput(
        trip_date=datetime(2026, 8, 15, tzinfo=KST).date(),
        accommodation=AccommodationInput(
            place_id="hotel-1",
            name="제주 숙소",
            coordinates=Coordinates(latitude=33.489, longitude=126.498),
        ),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 20, tzinfo=KST),
        ),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="transfer-1",
                type="transfer",
                start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 10, tzinfo=KST),
                from_place=PlaceReference(place_id="hotel-1", name="숙소"),
                to_place=PlaceReference(place_id="place-1", name="첫 장소"),
                planned_mode="bus",
                planned_route_number="202",
                planned_route_id="route-201",
                planned_boarding_stop_id="stop-a",
                planned_alighting_stop_id="stop-b",
                scheduled_departure_at=datetime(2026, 8, 15, 9, 10, tzinfo=KST),
                scheduled_arrival_at=datetime(2026, 8, 15, 9, 50, tzinfo=KST),
            ),
        ),
    )
    result = ItineraryEvaluationEngine(FixedEvidence()).evaluate(request)
    assert result.status == "infeasible"
    assert "USER_ROUTE_FACT_MISMATCH" in {issue.reason_code for issue in result.issues}


def test_full_timeline_accepts_taxi_pickup_buffer_before_first_transfer() -> None:
    """숙소 출발 택시 호출 대기는 첫 주행 전에 분리되어도 왕복 경계를 잃지 않아야 한다."""

    class TaxiEvidence(FixedEvidence):
        def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
            return RouteEvidence(
                mode="taxi",
                duration_minutes=20,
                distance_meters=8_000,
                cost_krw=8_000,
                walking_minutes=0,
                transfers=0,
                evidence_fact_ids=(f"fact-taxi-{from_place_id}-{to_place_id}",),
            )

    request = FullTimelineEvaluationInput(
        trip_date=datetime(2026, 8, 15, tzinfo=KST).date(),
        accommodation=AccommodationInput(place_id="hotel-1", name="숙소"),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 10, tzinfo=KST),
        ),
        transport=TransportPreferences(
            allowed_modes={"taxi"}, preferred_mode="taxi", fallback_order=()
        ),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineBuffer(
                event_id="pickup",
                type="buffer",
                start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 9, 10, tzinfo=KST),
                place=PlaceReference(place_id="hotel-1"),
                reason_code="TAXI_PICKUP_PLANNING_BUFFER_APPLIED",
                evidence_fact_ids=("fact-pickup-policy",),
            ),
            FullTimelineTransfer(
                event_id="outbound",
                type="transfer",
                start_at=datetime(2026, 8, 15, 9, 10, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 9, 30, tzinfo=KST),
                from_place=PlaceReference(place_id="hotel-1"),
                to_place=PlaceReference(place_id="place-1"),
                planned_mode="taxi",
                planned_distance_meters=8_000,
            ),
            FullTimelineTransfer(
                event_id="return",
                type="transfer",
                start_at=datetime(2026, 8, 15, 9, 30, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 9, 50, tzinfo=KST),
                from_place=PlaceReference(place_id="place-1"),
                to_place=PlaceReference(place_id="hotel-1"),
                planned_mode="taxi",
                planned_distance_meters=8_000,
            ),
        ),
    )

    result = ItineraryEvaluationEngine(TaxiEvidence()).evaluate(request)

    assert result.schedule_window_fit is True
    assert "TIMELINE_BOUNDARY_INCOMPLETE" not in {issue.reason_code for issue in result.issues}
    assert result.normalized_schedule.events[0].type == "buffer"


def test_full_timeline_requests_evidence_for_locked_planned_mode() -> None:
    """전체 일정 판정은 다른 빠른 수단이 아니라 사용자가 작성한 수단의 근거를 조회해야 한다."""

    class ModeAwareEvidence(FixedEvidence):
        def __init__(self) -> None:
            self.requested_modes: list[str] = []

        def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
            self.requested_modes.append(mode)
            return super().route(from_place_id, to_place_id, departure_at)

    evidence = ModeAwareEvidence()
    request = FullTimelineEvaluationInput(
        trip_date=datetime(2026, 8, 15, tzinfo=KST).date(),
        accommodation=AccommodationInput(place_id="hotel-1", name="숙소"),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 11, tzinfo=KST),
        ),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="transfer-1",
                type="transfer",
                start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 10, tzinfo=KST),
                from_place=PlaceReference(place_id="hotel-1", name="숙소"),
                to_place=PlaceReference(place_id="hotel-1", name="숙소"),
                planned_mode="bus",
                planned_route_id="route-201",
                planned_route_number="201",
                planned_boarding_stop_id="stop-a",
                planned_alighting_stop_id="stop-b",
                scheduled_departure_at=datetime(2026, 8, 15, 9, 10, tzinfo=KST),
                scheduled_arrival_at=datetime(2026, 8, 15, 9, 50, tzinfo=KST),
            ),
        ),
    )
    ItineraryEvaluationEngine(evidence).evaluate(request)
    assert evidence.requested_modes == ["bus"]


def test_full_timeline_does_not_turn_unknown_bus_distance_into_zero() -> None:
    """공식 버스 총거리가 없으면 0m 불일치가 아니라 검증 불가로 판정해야 한다."""

    class DistanceUnknownEvidence(FixedEvidence):
        def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
            route = super().route(from_place_id, to_place_id, departure_at)
            return RouteEvidence(**{**route.__dict__, "distance_meters": None})

    request = FullTimelineEvaluationInput(
        trip_date=datetime(2026, 8, 15, tzinfo=KST).date(),
        accommodation=AccommodationInput(place_id="hotel-1", name="숙소"),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 11, tzinfo=KST),
        ),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="transfer-1",
                type="transfer",
                start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 10, tzinfo=KST),
                from_place=PlaceReference(place_id="hotel-1", name="숙소"),
                to_place=PlaceReference(place_id="hotel-1", name="숙소"),
                planned_mode="bus",
                planned_distance_meters=12000,
                planned_route_id="route-201",
                planned_route_number="201",
                planned_boarding_stop_id="stop-a",
                planned_alighting_stop_id="stop-b",
                scheduled_departure_at=datetime(2026, 8, 15, 9, 10, tzinfo=KST),
                scheduled_arrival_at=datetime(2026, 8, 15, 9, 50, tzinfo=KST),
            ),
        ),
    )

    result = ItineraryEvaluationEngine(DistanceUnknownEvidence()).evaluate(request)
    codes = {issue.reason_code for issue in result.issues}
    assert result.status == "unverifiable"
    assert "USER_ROUTE_FACT_UNVERIFIABLE" in codes
    assert "USER_ROUTE_FACT_MISMATCH" not in codes


def test_evaluation_response_includes_provider_evidence_facts() -> None:
    """판정에 사용한 fact를 provider가 제공하면 응답 provenance에도 포함해야 한다."""

    class GroundedEvidence(FixedEvidence):
        def evidence_facts(self):
            return (*super().evidence_facts(), make_evidence_fact())

        def data_sources(self):
            return (make_data_source_metadata(),)

    result = ItineraryEvaluationEngine(GroundedEvidence()).evaluate(_request(12))
    assert "fact-place-open" in {fact.fact_id for fact in result.evidence_facts}


def test_only_explicit_total_budget_is_a_hard_cost_constraint() -> None:
    """택시 전용 상한 없이 명시한 전체 이동비 예산만 정확 일정의 비용 제약이어야 한다."""

    request = _request(12).model_copy(
        update={
            "transport": TransportPreferences(
                allowed_modes={"taxi"},
                preferred_mode="taxi",
                fallback_order=(),
            ),
            "total_budget_krw": 50000,
        }
    )
    result = ItineraryEvaluationEngine(ExpensiveWalkingEvidence()).evaluate(request)
    assert result.status == "infeasible"
    codes = {issue.reason_code for issue in result.issues}
    assert "TOTAL_BUDGET_EXCEEDED" in codes
    assert "TAXI_BUDGET_EXCEEDED" not in codes


def test_total_walking_distance_is_a_hard_constraint() -> None:
    """구간별 도보가 가능해도 하루 누적 도보거리 한도를 넘으면 불가능해야 한다."""

    class WalkingEvidence(FixedEvidence):
        def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
            return RouteEvidence(
                mode="walk",
                duration_minutes=20,
                distance_meters=2500,
                walking_distance_meters=2500,
                cost_krw=0,
                walking_minutes=20,
                transfers=0,
                evidence_fact_ids=(f"fact-walk-{from_place_id}-{to_place_id}",),
                stairs_status="CLEAR",
            )

    request = _request(12).model_copy(
        update={
            "transport": TransportPreferences(
                allowed_modes={"walk"}, preferred_mode="walk", fallback_order=()
            ),
            "walking": WalkingPreferences(max_total_distance_meters=6000),
        }
    )
    result = ItineraryEvaluationEngine(WalkingEvidence()).evaluate(request)
    assert result.status == "infeasible"
    assert "TOTAL_WALKING_LIMIT_EXCEEDED" in {issue.reason_code for issue in result.issues}


def test_shortening_repair_is_returned_only_after_revalidation() -> None:
    """음수 이동 여유 수정안은 체류 단축안을 다시 검증해 통과한 경우에만 반환해야 한다."""

    result = ItineraryEvaluationEngine(FixedEvidence()).evaluate(_request(11))
    assert result.repair_options
    assert result.repair_options[0].repair_type == "SHORTEN_STAY"
    assert result.repair_options[0].result_if_applied in {
        "feasible",
        "feasible_with_caution",
    }


def test_full_timeline_repairs_revalidate_shorten_skip_and_faster_mode() -> None:
    """전체 일정의 단축·선택 활동 제거·빠른 수단 수정안은 각각 전체 재평가를 통과해야 한다."""

    class RepairEvidence:
        evidence_facts = FixedEvidence.evidence_facts
        data_sources = FixedEvidence.data_sources

        def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
            if mode == "bus":
                return RouteEvidence(
                    mode="bus",
                    duration_minutes=10,
                    distance_meters=2_000,
                    cost_krw=1_200,
                    walking_minutes=2,
                    transfers=0,
                    evidence_fact_ids=("fact-fast-bus",),
                    route_number="201",
                    provider_route_id="route-201",
                    boarding_stop_id="stop-a",
                    alighting_stop_id="stop-b",
                    scheduled_departure_at=departure_at + timedelta(minutes=2),
                    scheduled_arrival_at=departure_at + timedelta(minutes=8),
                )
            return self.route(from_place_id, to_place_id, departure_at)

        def route(self, from_place_id, to_place_id, departure_at):
            duration = 40 if (from_place_id, to_place_id) == ("place-1", "hotel-1") else 20
            return RouteEvidence(
                mode="walk",
                duration_minutes=duration,
                distance_meters=1_000,
                cost_krw=0,
                walking_minutes=duration,
                walking_distance_meters=1_000,
                transfers=0,
                evidence_fact_ids=(f"fact-walk-{from_place_id}-{to_place_id}",),
                stairs_status="CLEAR",
            )

        def opening_window(self, place_id, on_date):
            start = datetime(2026, 8, 15, 9, tzinfo=KST)
            return start, start + timedelta(hours=3), ("fact-hours-place-1",)

    start = datetime(2026, 8, 15, 9, tzinfo=KST)
    request = FullTimelineEvaluationInput(
        trip_date=start.date(),
        accommodation=AccommodationInput(place_id="hotel-1", name="숙소"),
        activity_window=ActivityWindow(start_at=start, end_at=start + timedelta(minutes=80)),
        transport=TransportPreferences(
            allowed_modes={"walk", "bus"},
            preferred_mode="walk",
            fallback_order=("bus",),
        ),
        walking=WalkingPreferences(max_single_leg_minutes=60),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="inbound",
                type="transfer",
                start_at=start,
                end_at=start + timedelta(minutes=20),
                from_place=PlaceReference(place_id="hotel-1"),
                to_place=PlaceReference(place_id="place-1"),
                planned_mode="walk",
                planned_distance_meters=1_000,
            ),
            FullTimelineActivity(
                event_id="optional-visit",
                type="visit",
                start_at=start + timedelta(minutes=20),
                end_at=start + timedelta(minutes=60),
                place=PlaceReference(place_id="place-1"),
                required=False,
            ),
            FullTimelineTransfer(
                event_id="outbound",
                type="transfer",
                start_at=start + timedelta(minutes=60),
                end_at=start + timedelta(minutes=80),
                from_place=PlaceReference(place_id="place-1"),
                to_place=PlaceReference(place_id="hotel-1"),
                planned_mode="walk",
                planned_distance_meters=1_000,
                route_alternatives=(
                    RouteAlternativeSummary(
                        mode="bus",
                        duration_minutes=10,
                        walking_minutes=2,
                        cost=None,
                        status="feasible",
                        distance_meters=2_000,
                        selected=False,
                        evidence_fact_ids=("fact-fast-bus",),
                    ),
                ),
            ),
        ),
    )

    result = ItineraryEvaluationEngine(RepairEvidence()).evaluate(request)

    repair_by_type = {repair.repair_type: repair for repair in result.repair_options}
    assert {
        "SHORTEN_STAY",
        "SKIP_OPTIONAL_ACTIVITY",
        "USE_FASTER_VERIFIED_ALTERNATIVE",
    }.issubset(repair_by_type), (result.status, result.issues, result.segment_evaluations)
    assert all(
        repair.revalidated_evaluation is not None
        and repair.revalidated_evaluation.status in {"feasible", "feasible_with_caution"}
        for repair in repair_by_type.values()
    )


def test_full_timeline_bus_timeout_repair_inserts_taxi_pickup_and_revalidates() -> None:
    """버스 대기 초과 수정안은 택시 호출 버퍼를 삽입하고 전체 일정 재평가를 통과해야 한다."""

    class TaxiRecoveryEvidence:
        evidence_facts = FixedEvidence.evidence_facts
        data_sources = FixedEvidence.data_sources

        def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
            if mode == "bus":
                return RouteEvidence(
                    mode="bus",
                    duration_minutes=40,
                    distance_meters=4_000,
                    cost_krw=1_200,
                    walking_minutes=2,
                    transfers=0,
                    evidence_fact_ids=("fact-slow-bus",),
                    route_number="201",
                    provider_route_id="route-201",
                    boarding_stop_id="stop-a",
                    alighting_stop_id="stop-b",
                    scheduled_departure_at=departure_at + timedelta(minutes=2),
                    scheduled_arrival_at=departure_at + timedelta(minutes=35),
                )
            if mode == "taxi":
                return RouteEvidence(
                    mode="taxi",
                    duration_minutes=5,
                    distance_meters=4_000,
                    cost_krw=8_000,
                    cost_min_krw=7_000,
                    cost_max_krw=8_000,
                    walking_minutes=0,
                    transfers=0,
                    evidence_fact_ids=("fact-fast-taxi",),
                )
            return self.route(from_place_id, to_place_id, departure_at)

        def route(self, from_place_id, to_place_id, departure_at):
            return RouteEvidence(
                mode="walk",
                duration_minutes=20,
                distance_meters=1_000,
                cost_krw=0,
                walking_minutes=20,
                walking_distance_meters=1_000,
                transfers=0,
                evidence_fact_ids=(f"fact-walk-{from_place_id}-{to_place_id}",),
                stairs_status="CLEAR",
            )

        def opening_window(self, place_id, on_date):
            start = datetime(2026, 8, 15, 9, tzinfo=KST)
            return start, start + timedelta(hours=3), ("fact-hours-place-1",)

    start = datetime(2026, 8, 15, 9, tzinfo=KST)
    outbound_start = start + timedelta(minutes=60)
    request = FullTimelineEvaluationInput(
        trip_date=start.date(),
        accommodation=AccommodationInput(place_id="hotel-1", name="숙소"),
        activity_window=ActivityWindow(start_at=start, end_at=start + timedelta(minutes=80)),
        transport=TransportPreferences(
            allowed_modes={"walk", "bus", "taxi"},
            preferred_mode="bus",
            fallback_order=("taxi", "walk"),
        ),
        walking=WalkingPreferences(max_single_leg_minutes=60),
        schedule_format="full_timeline",
        timeline=(
            FullTimelineTransfer(
                event_id="inbound",
                type="transfer",
                start_at=start,
                end_at=start + timedelta(minutes=20),
                from_place=PlaceReference(place_id="hotel-1"),
                to_place=PlaceReference(place_id="place-1"),
                planned_mode="walk",
                planned_distance_meters=1_000,
            ),
            FullTimelineActivity(
                event_id="optional-visit",
                type="visit",
                start_at=start + timedelta(minutes=20),
                end_at=outbound_start,
                place=PlaceReference(place_id="place-1"),
            ),
            FullTimelineTransfer(
                event_id="outbound",
                type="transfer",
                start_at=outbound_start,
                end_at=start + timedelta(minutes=80),
                from_place=PlaceReference(place_id="place-1"),
                to_place=PlaceReference(place_id="hotel-1"),
                planned_mode="bus",
                planned_route_id="route-201",
                planned_route_number="201",
                planned_boarding_stop_id="stop-a",
                planned_alighting_stop_id="stop-b",
                scheduled_departure_at=outbound_start + timedelta(minutes=2),
                scheduled_arrival_at=outbound_start + timedelta(minutes=35),
                planned_distance_meters=4_000,
                route_alternatives=(
                    RouteAlternativeSummary(
                        mode="taxi",
                        duration_minutes=15,
                        walking_minutes=0,
                        cost=CostRange(min_krw=7_000, max_krw=8_000, is_estimated=True),
                        status="feasible",
                        distance_meters=4_000,
                        selected=False,
                        evidence_fact_ids=("fact-fast-taxi",),
                    ),
                ),
            ),
        ),
    )

    result = ItineraryEvaluationEngine(TaxiRecoveryEvidence()).evaluate(request)
    repair = next(
        item for item in result.repair_options if item.repair_type == "TAXI_AFTER_BUS_TIMEOUT"
    )

    assert repair.revalidated_evaluation is not None
    assert repair.revalidated_evaluation.status in {"feasible", "feasible_with_caution"}
    assert repair.cost_increase.min_krw == 5_800


def test_required_rest_is_hard_without_restroom_data() -> None:
    """화장실 데이터 없이도 사용자가 요구한 휴식은 연속 활동 하드 제약이어야 한다."""

    request = _request(12).model_copy(
        update={
            "rest": RestPreferences(
                max_continuous_activity_minutes=60,
                seat_requirement="required",
                restroom_requirement="not_needed",
                indoor_requirement="not_needed",
            )
        }
    )
    result = ItineraryEvaluationEngine(FixedEvidence()).evaluate(request)
    assert result.status == "infeasible"
    assert "MISSING_REQUIRED_REST" in {issue.reason_code for issue in result.issues}


def test_unknown_opening_hours_are_partial_evidence_not_timing_conflict() -> None:
    """운영시간 미확인은 시간 정상 상태를 유지하고 근거만 부분 상태여야 한다."""

    class UnknownOpeningEvidence:
        def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
            return FixedEvidence().route(from_place_id, to_place_id, departure_at)

        def opening_window(self, place_id: str, on_date):
            return None

        evidence_facts = FixedEvidence.evidence_facts
        data_sources = FixedEvidence.data_sources

    result = ItineraryEvaluationEngine(UnknownOpeningEvidence()).evaluate(_request(12))

    assert result.timing_status == "on_schedule"
    assert result.evidence_status == "partial"
    assert result.status == "feasible_with_caution"
    assert all(
        activity.operating_hours_status == "UNVERIFIABLE"
        for activity in result.activity_evaluations
    )


def test_excluded_foods_are_checked_with_allergens_against_dietary_evidence() -> None:
    """제외음식만 있는 식사도 알레르겐과 함께 exact 검증 fact 조회에 전달해야 한다."""

    class DietaryEvidence(FixedEvidence):
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

        def dietary_safety(self, place_id, allergens, excluded_foods, on_date):
            self.calls.append((place_id, allergens, excluded_foods))
            return (True, ("fact-dietary-safe",))

    base = _request(12)
    meal = base.scheduled_activities[1].model_copy(update={"type": "meal"})
    request = base.model_copy(
        update={
            "food": base.food.model_copy(update={"excluded_foods": ("돼지고기",)}),
            "scheduled_activities": (base.scheduled_activities[0], meal),
        }
    )
    evidence = DietaryEvidence()

    ItineraryEvaluationEngine(evidence).evaluate(request)

    assert evidence.calls == [("place-2", (), ("돼지고기",))]


def test_evaluation_preserves_transport_cost_ranges() -> None:
    """판정 합계는 구간 이동비 최소·최대 범위를 최대값 하나로 축약하지 않아야 한다."""

    class RangeCostEvidence(FixedEvidence):
        def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
            route = super().route(from_place_id, to_place_id, departure_at)
            return RouteEvidence(
                **{
                    **route.__dict__,
                    "cost_min_krw": 1_000,
                    "cost_max_krw": 2_000,
                }
            )

    result = ItineraryEvaluationEngine(RangeCostEvidence()).evaluate(_request(12))

    assert result.total_cost.min_krw == 3_000
    assert result.total_cost.max_krw == 6_000
    assert result.totals.transport_cost == result.total_cost
