"""공개 계약 테스트용 최소 유효 객체 생성기."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from jeju_trip.domain.models import (
    AccommodationInput,
    ActivityWindow,
    Coordinates,
    CostRange,
    DataSourceMetadata,
    DayTripResponse,
    Derivation,
    EvidenceFact,
    GroundedReason,
    MealDetails,
    PlanningContext,
    Recommendation,
    RecommendationScore,
    RecommendDayTripsInput,
    RestDetails,
    ScoreComponent,
    ScoreComponentName,
    SourceRef,
    Strategy,
    TimelineEvent,
    Totals,
    Transfer,
    TransportSelectionSummary,
    ValidationSummary,
    VisitDetails,
    WalkConnection,
)

KST = timezone(timedelta(hours=9))
TRIP_DATE = date(2026, 8, 15)


def make_request() -> RecommendDayTripsInput:
    return RecommendDayTripsInput(
        trip_date=TRIP_DATE,
        accommodation=AccommodationInput(name="제주국제공항"),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 20, tzinfo=KST),
        ),
    )


def make_evidence_fact(fact_id: str = "fact-place-open") -> EvidenceFact:
    retrieved_at = datetime(2026, 7, 21, 10, tzinfo=KST)
    return EvidenceFact(
        fact_id=fact_id,
        category="opening_hours",
        value="09:00-18:00",
        source_refs=(
            SourceRef(
                source_id="tourapi.place",
                publication_id="publication-place-1",
                source_fact_id=fact_id,
            ),
        ),
        data_as_of=date(2026, 7, 20),
        retrieved_at=retrieved_at,
        confidence=0.9,
        derivation=Derivation(kind="source"),
    )


def make_data_source_metadata() -> DataSourceMetadata:
    return DataSourceMetadata(
        source_id="tourapi.place",
        provider="한국관광공사",
        dataset_version="fixture-v1",
        data_as_of=date(2026, 7, 20),
        retrieved_at=datetime(2026, 7, 21, 10, tzinfo=KST),
        status="ACTIVE",
        attribution_text="한국관광공사 TourAPI",
    )


def make_recommendation(strategy: Strategy, rank: int) -> Recommendation:
    starts_at = datetime(2026, 8, 15, 10 + rank, tzinfo=KST)
    fact_ids = ("fact-place-open",)
    score_weights: dict[ScoreComponentName, int] = {
        "preferred_places": 25,
        "travel_efficiency": 20,
        "reliability": 20,
        "comfort": 15,
        "cost_efficiency": 10,
        "data_confidence": 10,
    }
    visit = TimelineEvent(
        event_id=f"visit-{rank}",
        sequence=1,
        type="visit",
        start_at=starts_at,
        end_at=starts_at + timedelta(minutes=60),
        duration_minutes=60,
        title=f"동부권 장소 {rank}",
        place_id=f"place-{rank}",
        visit=VisitDetails(
            place_id=f"place-{rank}",
            name=f"동부권 장소 {rank}",
            position=Coordinates(latitude=33.45 + rank / 100, longitude=126.7),
            entrance_id=f"entrance-{rank}",
            arrival_at=starts_at,
            entry_at=starts_at,
            departure_at=starts_at + timedelta(minutes=60),
            stay_minutes=60,
            operating_hours_status="VERIFIED",
            opens_at=starts_at - timedelta(hours=2),
            closes_at=starts_at + timedelta(hours=5),
            last_admission_at=starts_at + timedelta(hours=4),
            evidence_fact_ids=fact_ids,
        ),
        evidence_fact_ids=fact_ids,
    )
    walk = WalkConnection(
        kind="direct_walk",
        from_id=f"entrance-{rank}",
        to_id=f"rest-{rank}",
        distance_meters=800,
        expected_minutes=12,
        speed_multiplier=1.15,
        route_uncertainty_minutes=2,
        planned_minutes=16,
        entrance_verification="VERIFIED",
        evidence_fact_ids=fact_ids,
    )
    transfer = TimelineEvent(
        event_id=f"transfer-{rank}",
        sequence=2,
        type="transfer",
        start_at=visit.end_at,
        end_at=visit.end_at + timedelta(minutes=20),
        duration_minutes=20,
        title="다음 장소로 도보 이동",
        transfer=Transfer(mode="walk", direct_walk=walk),
        evidence_fact_ids=fact_ids,
    )
    rest = TimelineEvent(
        event_id=f"rest-{rank}",
        sequence=3,
        type="rest",
        start_at=transfer.end_at,
        end_at=transfer.end_at + timedelta(minutes=20),
        duration_minutes=20,
        title="검증된 휴식 시간",
        rest=RestDetails(
            place_id=f"rest-{rank}",
            seat="AVAILABLE",
            restroom="UNKNOWN",
            indoor="AVAILABLE",
            evidence_fact_ids=fact_ids,
        ),
        evidence_fact_ids=fact_ids,
    )
    meal = TimelineEvent(
        event_id=f"meal-{rank}",
        sequence=4,
        type="meal",
        start_at=rest.end_at,
        end_at=rest.end_at + timedelta(minutes=50),
        duration_minutes=50,
        title="점심 식사",
        meal=MealDetails(
            place_id=f"meal-{rank}",
            venue_name=f"식사 장소 {rank}",
            evidence_fact_ids=fact_ids,
        ),
        evidence_fact_ids=fact_ids,
    )
    return Recommendation(
        route_id=f"route-{strategy.value}",
        rank=rank,
        strategy=strategy,
        title=f"{strategy.value} 일정",
        score=RecommendationScore(
            total=100,
            components={
                key: ScoreComponent(
                    value=100,
                    weight=weight,
                    weighted_value=weight,
                    evidence_fact_ids=fact_ids,
                )
                for key, weight in score_weights.items()
            },
        ),
        recommendation_reasons=(
            GroundedReason(
                text="검증된 운영시간 안에 방문합니다.",
                evidence_fact_ids=("fact-place-open",),
            ),
        ),
        place_ids=(f"place-{rank}",),
        feasibility="feasible",
        accommodation_departure_at=visit.start_at,
        accommodation_return_at=meal.end_at,
        timeline=(visit, transfer, rest, meal),
        totals=Totals(
            total_minutes=150,
            visit_minutes=60,
            transfer_minutes=20,
            rest_minutes=20,
            meal_minutes=50,
            buffer_minutes=0,
            walking_minutes=16,
            walking_distance_meters=800,
            total_distance_meters=800,
            estimated_cost=CostRange(min_krw=0, max_krw=0, is_estimated=False),
            derivation_evidence_fact_ids=fact_ids,
        ),
        transport_selection_summary=TransportSelectionSummary(
            selected_walk_legs=1,
            selected_bus_legs=0,
            selected_taxi_legs=0,
            feasible_bus_alternatives=0,
            unverifiable_bus_alternatives=0,
        ),
    )


def make_success_response() -> DayTripResponse:
    now = datetime(2026, 7, 21, 10, tzinfo=KST)
    return DayTripResponse(
        request_id="req-test-success",
        generated_at=now,
        status="success",
        planning_context=PlanningContext(
            planned_at=now,
            trip_date=TRIP_DATE,
            days_before_trip=25,
            schedule_basis="future_timetable",
            plan_expires_at=now + timedelta(days=1),
        ),
        request=make_request(),
        recommendations=tuple(
            make_recommendation(strategy, rank) for rank, strategy in enumerate(Strategy, start=1)
        ),
        evidence_facts=(make_evidence_fact(),),
        data_sources=(make_data_source_metadata(),),
        validation=ValidationSummary(
            schema_valid=True,
            timeline_valid=True,
            provenance_valid=True,
            transit_connections_valid=True,
            diversity_valid=True,
        ),
    )
