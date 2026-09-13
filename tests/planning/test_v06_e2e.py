"""v0.6 생성·판정·재검증과 최대 5일 이력의 통합 회귀 테스트."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jeju_trip.application.service import TripPlannerService
from jeju_trip.domain.models import (
    ActivityWindow,
    BusRide,
    Coordinates,
    ProgressInput,
    RecommendDayTripsInput,
    RevalidateJejuDayTripInput,
    SelectedDayHistory,
    SelectedPlaceHistory,
    Strategy,
    Transfer,
    WalkConnection,
)
from jeju_trip.planning.evaluation import ItineraryEvaluationEngine, RouteEvidence
from jeju_trip.planning.generation import (
    DeterministicDayTripGenerator,
    DynamicClusterCandidateAssembler,
    VerifiedRouteOption,
)
from jeju_trip.planning.policy import load_planning_policy
from jeju_trip.planning.timeline_conversion import recommendation_to_full_timeline
from tests.factories import make_data_source_metadata, make_evidence_fact, make_recommendation
from tests.planning.test_generation import FixedGenerationGateway, FixedOrderProposer, _request

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[2]


class GeneratedTimelineEvidence:
    """생성 fixture와 같은 15분 도보 및 하루 운영시간을 독립 조회로 제공한다."""

    def __init__(self) -> None:
        self._fact_ids: set[str] = set()

    def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
        fact_id = f"fact-route-{from_place_id}-{to_place_id}"
        self._fact_ids.add(fact_id)
        return RouteEvidence(
            mode="walk",
            duration_minutes=15,
            distance_meters=500,
            cost_krw=0,
            walking_minutes=15,
            walking_distance_meters=500,
            transfers=0,
            evidence_fact_ids=(fact_id,),
        )

    def opening_window(self, place_id: str, on_date: date):
        fact_id = f"fact-{place_id}"
        self._fact_ids.add(fact_id)
        start = datetime.combine(on_date, datetime.min.time(), tzinfo=KST)
        return start + timedelta(hours=9), start + timedelta(hours=20), (fact_id,)

    def evidence_facts(self):
        """실제 조회된 합성 fact ID에 대응하는 ledger를 판정 응답에 제공한다."""

        return tuple(make_evidence_fact(fact_id) for fact_id in sorted(self._fact_ids))

    def data_sources(self):
        """생성 fixture의 source fact에 대응하는 source metadata를 제공한다."""

        return (make_data_source_metadata(),)


def _history(day: date, visited_place_id: str, index: int) -> SelectedDayHistory:
    source = make_recommendation(Strategy.BALANCED, 1)
    offset = day - source.timeline[0].start_at.date()
    shifted = []
    for event in source.timeline:
        update = {
            "event_id": f"history-{index}-{event.event_id}",
            "start_at": event.start_at + offset,
            "end_at": event.end_at + offset,
        }
        if event.visit is not None:
            update.update(
                {
                    "place_id": visited_place_id,
                    "visit": event.visit.model_copy(
                        update={
                            "place_id": visited_place_id,
                            "arrival_at": event.visit.arrival_at + offset,
                            "entry_at": event.visit.entry_at + offset,
                            "departure_at": event.visit.departure_at + offset,
                            "opens_at": (
                                event.visit.opens_at + offset if event.visit.opens_at else None
                            ),
                            "closes_at": (
                                event.visit.closes_at + offset if event.visit.closes_at else None
                            ),
                            "last_admission_at": (
                                event.visit.last_admission_at + offset
                                if event.visit.last_admission_at
                                else None
                            ),
                        }
                    ),
                }
            )
        shifted.append(event.model_copy(update=update))
    recommendation = source.model_copy(
        update={
            "route_id": f"history-route-{index}",
            "place_ids": (visited_place_id,),
            "day_start_at": source.day_start_at + offset,
            "day_end_at": source.day_end_at + offset,
            "accommodation_departure_at": source.accommodation_departure_at + offset,
            "accommodation_return_at": source.accommodation_return_at + offset,
            "timeline": tuple(shifted),
        }
    )
    return SelectedDayHistory(
        trip_date=day,
        activity_window=ActivityWindow(
            start_at=datetime.combine(day, datetime.min.time(), tzinfo=KST)
            + timedelta(hours=9),
            end_at=datetime.combine(day, datetime.min.time(), tzinfo=KST) + timedelta(hours=20),
        ),
        day_start_at=recommendation.day_start_at,
        day_end_at=recommendation.day_end_at,
        selected_places=(
            SelectedPlaceHistory(
                place_id=visited_place_id,
                role="visit",
                evidence_fact_ids=("fact-place-open",),
            ),
            SelectedPlaceHistory(
                place_id="meal-1", role="meal", evidence_fact_ids=("fact-place-open",)
            ),
            SelectedPlaceHistory(
                place_id="rest-1", role="rest", evidence_fact_ids=("fact-place-open",)
            ),
        ),
        totals=recommendation.totals,
        evidence_fact_ids=("fact-place-open",),
    )


def test_generate_evaluate_revalidate_preserves_one_timeline() -> None:
    """여러 인원 구성도 세 추천·판정·재검증에서 사건과 근거가 손실되지 않아야 한다."""

    base = _request()
    request = base.model_copy(
        update={
            "party": base.party.model_copy(
                update={"adults": 2, "children": 1, "seniors": 1}
            )
        }
    )
    generated = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))
    assert generated.status == "success"
    assert len(generated.recommendations) == 3
    recommendation = next(
        item for item in generated.recommendations if item.strategy == Strategy.BALANCED
    )
    timeline = recommendation_to_full_timeline(request, recommendation)
    evidence = GeneratedTimelineEvidence()
    evaluated = ItineraryEvaluationEngine(evidence).evaluate(timeline)
    first_activity = next(item for item in timeline.timeline if item.type == "visit")
    revalidated = TripPlannerService(evaluation_evidence=evidence).revalidate(
        RevalidateJejuDayTripInput(
            checked_at=first_activity.start_at,
            progress=ProgressInput(
                state="at_place",
                current_event_id=first_activity.event_id,
                actual_time=first_activity.start_at,
                current_place_id=first_activity.place.place_id,
                current_event_started_at=first_activity.start_at,
            ),
            itinerary=timeline,
        )
    )

    assert evaluated.status == "feasible_with_caution"
    assert evaluated.timing_status == "at_risk"
    assert revalidated.timing_status == "at_risk"
    assert revalidated.original_evaluation.normalized_schedule == evaluated.normalized_schedule
    assert all(
        transfer.selected_transfer is not None
        for transfer in timeline.timeline
        if transfer.type == "transfer"
    )


def test_fifth_day_generation_excludes_all_previous_visits() -> None:
    """다섯째 날 생성은 앞선 네 날짜의 관광지를 모두 제외하고 서로 다른 세 일정을 만들어야 한다."""

    class FiveDayGateway(FixedGenerationGateway):
        """이전 관광지 제거 뒤에도 세 전략에 충분한 전역 후보를 제공한다."""

        def __init__(self) -> None:
            super().__init__()
            for index, place_id in enumerate(("c", "d", "e", "f"), start=1):
                self._places[place_id] = replace(
                    self._places["b"],
                    place_id=place_id,
                    name=place_id,
                    position=Coordinates(
                        latitude=33.50 + index / 100,
                        longitude=126.72 + index / 100,
                    ),
                    evidence_fact_ids=(f"fact-{place_id}",),
                )

    base = _request().model_dump(mode="python")
    histories = tuple(
        _history(day, place_id, index)
        for index, (day, place_id) in enumerate(
            zip(
                (date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)),
                ("old-1", "old-2", "old-3", "a"),
                strict=True,
            ),
            start=1,
        )
    )
    base["previous_days"] = tuple(item.model_dump(mode="python") for item in histories)
    request = RecommendDayTripsInput.model_validate(base)
    response = DeterministicDayTripGenerator(
        FiveDayGateway(),
        DynamicClusterCandidateAssembler(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success", response.failure
    previous_visits = {"old-1", "old-2", "old-3", "a"}
    assert len(response.recommendations) == 3
    assert all(
        previous_visits.isdisjoint(recommendation.place_ids)
        for recommendation in response.recommendations
    )


def test_bus_only_generation_verifies_every_leg_without_taxi() -> None:
    """버스 전용 성공 결과의 숙소 왕복 포함 모든 구간은 bus ride 근거를 가져야 한다."""

    class BusOnlyGateway(FixedGenerationGateway):
        """세 전략을 완주할 수 있는 exact 버스 fixture를 제공한다."""

        def __init__(self) -> None:
            super().__init__()
            for index, place_id in enumerate(("c", "d", "e"), start=1):
                self._places[place_id] = replace(
                    self._places["b"],
                    place_id=place_id,
                    name=place_id,
                    position=Coordinates(
                        latitude=33.50 + index / 100,
                        longitude=126.72 + index / 100,
                    ),
                    evidence_fact_ids=(f"fact-{place_id}",),
                )

        def route(self, from_id, to_id, departure_at, strategy, request, budget):
            budget.claim_external_call("fixture-bus-routing")
            fact_id = f"fact-route-{from_id}-{to_id}"
            access = WalkConnection(
                kind="access_walk",
                from_id=from_id,
                to_id=f"stop-{from_id}",
                distance_meters=50,
                expected_minutes=0,
                speed_multiplier=1,
                route_uncertainty_minutes=1,
                planned_minutes=1,
                entrance_verification="VERIFIED",
                evidence_fact_ids=(fact_id,),
            )
            egress = WalkConnection(
                kind="egress_walk",
                from_id=f"stop-{to_id}",
                to_id=to_id,
                distance_meters=50,
                expected_minutes=0,
                speed_multiplier=1,
                route_uncertainty_minutes=1,
                planned_minutes=1,
                entrance_verification="VERIFIED",
                evidence_fact_ids=(fact_id,),
            )
            scheduled_departure = departure_at + timedelta(minutes=8)
            scheduled_arrival = departure_at + timedelta(minutes=9)
            ride = BusRide(
                canonical_boarding_stop_id=f"stop-{from_id}",
                provider_boarding_stop_id=f"provider-{from_id}",
                boarding_stop_name=f"{from_id} 정류장",
                boarding_stop_position=Coordinates(latitude=33.5, longitude=126.5),
                boarding_direction="순환 방향",
                canonical_alighting_stop_id=f"stop-{to_id}",
                provider_alighting_stop_id=f"provider-{to_id}",
                alighting_stop_name=f"{to_id} 정류장",
                alighting_stop_position=Coordinates(latitude=33.51, longitude=126.51),
                route_id=f"route-{from_id}-{to_id}",
                route_number="순환",
                scheduled_departure_at=scheduled_departure,
                scheduled_arrival_at=scheduled_arrival,
                recommended_stop_arrival_at=scheduled_departure - timedelta(minutes=7),
                boarding_buffer_minutes=7,
                mapping_status="CONFIRMED",
                evidence_fact_ids=(fact_id,),
            )
            return VerifiedRouteOption(
                from_id=from_id,
                to_id=to_id,
                duration_minutes=10,
                walking_minutes=2,
                walking_distance_meters=100,
                cost_min_krw=1_150,
                cost_max_krw=1_200,
                transfers=0,
                transfer=Transfer(
                    mode="bus",
                    access_walk=access,
                    bus_rides=(ride,),
                    egress_walk=egress,
                    distance_meters=100,
                    distance_is_estimated=True,
                ),
                evidence_fact_ids=(fact_id,),
            )

    payload = _request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "selection_policy": "cost_time_balance",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
    }
    request = RecommendDayTripsInput.model_validate(payload)
    response = DeterministicDayTripGenerator(
        BusOnlyGateway(),
        DynamicClusterCandidateAssembler(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success", response.failure
    assert len(response.recommendations) == 3
    transfers = [
        event.transfer
        for recommendation in response.recommendations
        for event in recommendation.timeline
        if event.transfer is not None
    ]
    assert transfers
    assert all(transfer.mode == "bus" and transfer.bus_rides for transfer in transfers)
