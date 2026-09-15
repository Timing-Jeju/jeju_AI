"""근거 기반 세 전략 일정 생성 파이프라인 테스트."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from jeju_trip.domain.models import (
    Coordinates,
    Derivation,
    EndpointBasis,
    EvidenceFact,
    ModeDecision,
    PlaceReference,
    RecommendDayTripsInput,
    Strategy,
    TaxiAlternative,
    Transfer,
    WalkConnection,
)
from jeju_trip.infrastructure.tmap_cache import MAX_TTL_SECONDS
from jeju_trip.planning.generation import (
    DeterministicDayTripGenerator,
    DynamicClusterCandidateAssembler,
    ScopedTemplateCandidateAssembler,
    VerifiedGenerationPlace,
    VerifiedOpeningWindow,
    VerifiedRouteOption,
)
from jeju_trip.planning.policy import load_planning_policy
from tests.factories import make_request

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[2]


class FixedGenerationGateway:
    """세 전략 생성에 필요한 검증 장소·경로 fact를 제공한다."""

    def __init__(self) -> None:
        opens = datetime(2026, 8, 15, 9, tzinfo=KST)
        closes = datetime(2026, 8, 15, 19, tzinfo=KST)
        self._places = {
            place_id: VerifiedGenerationPlace(
                place_id=place_id,
                name=place_id,
                position=Coordinates(latitude=33.45 + index / 100, longitude=126.7),
                entrance_id=f"entrance-{place_id}",
                category="nature",
                opens_at=opens,
                closes_at=closes,
                last_admission_at=closes - timedelta(minutes=30),
                stay_minutes=60,
                is_estimated_stay=True,
                evidence_fact_ids=(f"fact-{place_id}",),
            )
            for index, place_id in enumerate(("required", "a", "b"), start=1)
        }
        self._places["meal"] = VerifiedGenerationPlace(
            place_id="meal",
            name="검증 식당",
            position=Coordinates(latitude=33.46, longitude=126.71),
            entrance_id="entrance-meal",
            category="39",
            opens_at=opens,
            closes_at=closes,
            last_admission_at=None,
            stay_minutes=60,
            is_estimated_stay=False,
            evidence_fact_ids=("fact-meal",),
            activity_type="meal",
            last_order_at=closes - timedelta(minutes=30),
        )
        self._places["rest"] = VerifiedGenerationPlace(
            place_id="rest",
            name="검증 카페",
            position=Coordinates(latitude=33.47, longitude=126.72),
            entrance_id="entrance-rest",
            category="39",
            opens_at=opens,
            closes_at=closes,
            last_admission_at=None,
            stay_minutes=20,
            is_estimated_stay=False,
            evidence_fact_ids=("fact-rest",),
            activity_type="rest",
            seat="AVAILABLE",
            indoor="AVAILABLE",
        )

    def places(self, request: RecommendDayTripsInput):
        return tuple(self._places.values())

    def route(self, from_id, to_id, departure_at, strategy, request, budget):
        budget.claim_external_call("fixture-routing")
        fact_id = f"fact-route-{from_id}-{to_id}"
        walk = WalkConnection(
            kind="direct_walk",
            from_id=from_id,
            to_id=to_id,
            distance_meters=500,
            expected_minutes=10,
            speed_multiplier=1.15,
            route_uncertainty_minutes=3,
            planned_minutes=15,
            entrance_verification="VERIFIED",
            evidence_fact_ids=(fact_id,),
        )
        return VerifiedRouteOption(
            from_id=from_id,
            to_id=to_id,
            duration_minutes=15,
            walking_minutes=15,
            walking_distance_meters=500,
            cost_min_krw=0,
            cost_max_krw=0,
            transfers=0,
            transfer=Transfer(mode="walk", direct_walk=walk, distance_meters=500),
            evidence_fact_ids=(fact_id,),
        )

    def evidence_facts(self):
        ids = [f"fact-{place_id}" for place_id in self._places]
        ids.extend(
            f"fact-route-{left}-{right}"
            for left in ("hotel", "airport", "port", *self._places)
            for right in ("hotel", "airport", "port", *self._places)
            if left != right
        )
        ids.extend(
            (
                "fact-policy-meal",
                "fact-policy-rest",
                "fact-policy-risk-high",
                "fact-policy-risk-medium",
                "fact-policy-score",
            )
        )
        now = datetime(2026, 8, 10, 10, tzinfo=KST)
        return tuple(
            EvidenceFact(
                fact_id=fact_id,
                category="fixture",
                value=True,
                data_as_of=date(2026, 8, 10),
                retrieved_at=now,
                confidence=1,
                derivation=Derivation(kind="policy"),
            )
            for fact_id in ids
        )

    def data_sources(self):
        return ()

    def policy_fact_id(self, policy_key):
        return {
            "meal.default_duration_minutes": "fact-policy-meal",
            "rest.minimum_break_minutes": "fact-policy-rest",
            "risk_slack_minutes.high_upper_exclusive": "fact-policy-risk-high",
            "risk_slack_minutes.medium_upper_exclusive": "fact-policy-risk-medium",
            "score.weights.balanced": "fact-policy-score",
            "score.weights.relaxed": "fact-policy-score",
            "score.weights.experience_max": "fact-policy-score",
        }.get(policy_key)


class FixedOrderProposer:
    """LLM 대신 장소 ID 순서만 제안한다."""

    def propose(self, request, places):
        return {
            Strategy.BALANCED: ("required", "meal", "a", "rest"),
            Strategy.RELAXED: ("required", "meal", "b", "rest"),
            Strategy.EXPERIENCE_MAX: ("required", "meal", "a", "b", "rest"),
        }


def _request() -> RecommendDayTripsInput:
    base = make_request().model_dump(mode="python")
    base["accommodation"] = {
        "place_id": "hotel",
        "name": "제주 숙소",
        "coordinates": {"latitude": 33.49, "longitude": 126.5},
    }
    base["required_places"] = ({"place_id": "required", "name": "필수"},)
    base["preferred_places"] = (
        {"place_id": "a", "name": "선호 A"},
        {"place_id": "b", "name": "선호 B"},
    )
    return RecommendDayTripsInput.model_validate(base)


def test_scoped_template_adds_required_places_after_first_two_steps() -> None:
    """추가 필수 장소는 세 전략의 첫 두 핵심 단계를 보존한 뒤 일정에 들어가야 한다."""

    class TemplateGateway(FixedGenerationGateway):
        """고정 active template 후보를 제공한다."""

        def template_candidate_groups(self, request):
            return {
                Strategy.BALANCED: (
                    ("required",),
                    ("meal", "a"),
                    ("a",),
                    ("rest", "b"),
                ),
                Strategy.RELAXED: (
                    ("required",),
                    ("meal", "a"),
                    ("b",),
                    ("rest", "b"),
                ),
                Strategy.EXPERIENCE_MAX: (
                    ("required",),
                    ("meal", "a"),
                    ("a",),
                    ("b",),
                    ("rest", "b"),
                ),
            }

    request = _request().model_copy(
        update={
            "required_places": (
                *_request().required_places,
                PlaceReference(place_id="far-required", name="추가 필수 장소"),
            )
        }
    )
    places = tuple(FixedGenerationGateway()._places.values()) + (
        replace(FixedGenerationGateway()._places["a"], place_id="far-required"),
    )

    orders = ScopedTemplateCandidateAssembler(TemplateGateway()).propose(request, places)

    assert orders[Strategy.BALANCED] == ("required", "meal", "far-required", "rest")
    assert orders[Strategy.RELAXED] == ("required", "meal", "far-required", "b")
    assert orders[Strategy.EXPERIENCE_MAX] == (
        "required",
        "a",
        "far-required",
        "rest",
    )


def test_dynamic_cluster_assembler_builds_distinct_strategy_skeletons() -> None:
    """전역 후보 조립기는 전략별 관광지 수와 장소 조합을 실질적으로 다르게 만들어야 한다."""

    gateway = FixedGenerationGateway()
    visits = tuple(gateway._places.values()) + tuple(
        replace(
            gateway._places["a"],
            place_id=f"extra-{index}",
            position=Coordinates(latitude=33.50 + index / 100, longitude=126.75),
        )
        for index in range(3)
    )

    orders = DynamicClusterCandidateAssembler().propose(_request(), visits)

    assert len(set(orders.values())) == 3
    assert len([item for item in orders[Strategy.RELAXED] if item not in {"meal", "rest"}]) == 2
    assert len([item for item in orders[Strategy.BALANCED] if item not in {"meal", "rest"}]) == 3
    assert (
        len([item for item in orders[Strategy.EXPERIENCE_MAX] if item not in {"meal", "rest"}]) == 4
    )


def test_dynamic_cluster_assembler_uses_exact_bus_candidate_orders() -> None:
    """버스 전용 요청은 좌표 근접순이 아니라 gateway의 exact stop-time 순서를 사용해야 한다."""

    class BusCandidateGateway:
        """전략별 exact 버스 primary와 fallback을 제공한다."""

        def bus_only_candidate_orders(self, request, places):
            return {
                Strategy.RELAXED: (("required", "meal", "b", "rest"),),
                Strategy.BALANCED: (("required", "meal", "a", "rest"),),
                Strategy.EXPERIENCE_MAX: (("required", "meal", "a", "b", "rest"),),
            }

    payload = _request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "selection_policy": "cost_time_balance",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
    }
    request = RecommendDayTripsInput.model_validate(payload)
    gateway = FixedGenerationGateway()

    candidates = DynamicClusterCandidateAssembler(BusCandidateGateway()).propose_candidates(
        request, tuple(gateway._places.values())
    )

    assert candidates[Strategy.RELAXED][0] == ("required", "meal", "b", "rest")
    assert candidates[Strategy.BALANCED][0] == ("required", "meal", "a", "rest")


def test_generator_tries_only_bounded_fallback_for_failed_strategy_candidate() -> None:
    """전략 primary가 구조적으로 실패하면 같은 전략의 두 번째 후보까지만 재시도해야 한다."""

    class CandidateProposer:
        """존재하지 않는 primary와 검증 가능한 fallback을 함께 제공한다."""

        def propose_candidates(self, request, places):
            return {
                Strategy.BALANCED: (
                    ("unknown",),
                    ("required", "meal", "a", "rest"),
                ),
                Strategy.RELAXED: (
                    ("unknown",),
                    ("required", "meal", "b", "rest"),
                ),
                Strategy.EXPERIENCE_MAX: (
                    ("unknown",),
                    ("required", "meal", "a", "b", "rest"),
                ),
            }

    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        CandidateProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success", response.failure
    assert len(response.recommendations) == 3


def test_generated_plan_expires_with_ephemeral_route_evidence() -> None:
    """생성 결과의 만료시각은 TMAP 파생 근거의 23시간 50분 메모리 TTL을 넘지 않아야 한다."""

    generated_at = datetime(2026, 8, 10, 10, tzinfo=KST)
    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=generated_at)

    assert response.planning_context.plan_expires_at <= generated_at + timedelta(
        seconds=MAX_TTL_SECONDS
    )


def test_experience_max_uses_versioned_maximum_for_last_rest() -> None:
    """experience_max 마지막 휴식 차이는 임의 분 수가 아니라 정책 maximum이어야 한다."""

    gateway = FixedGenerationGateway()
    generator = DeterministicDayTripGenerator(
        gateway,
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    )

    adjusted = generator._strategy_place_durations(
        _request(),
        Strategy.EXPERIENCE_MAX,
        ("a", "meal", "rest"),
        gateway._places,
        set(),
    )

    assert adjusted["a"].stay_minutes == 60
    assert gateway._places["rest"].stay_minutes == 20
    assert adjusted["rest"].stay_minutes == 60


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({}, id="기존_사용자_지정"),
        pytest.param(
            {
                "source": "place_override",
                "policy_version": "stay-v1",
                "policy_effective_at": "2026-08-01T00:00:00Z",
            },
            id="서버_장소별_정책",
        ),
        pytest.param(
            {
                "source": "category_default",
                "policy_version": "stay-v1",
                "policy_effective_at": "2026-08-01T00:00:00Z",
            },
            id="서버_분류별_정책",
        ),
    ],
)
def test_distinct_day_boundaries_and_requested_stay_are_preserved(metadata: dict[str, str]) -> None:
    """terminal 경계와 사용자 또는 검증된 서버 체류시간은 세 전략 모두 보존해야 한다."""

    payload = _request().model_dump(mode="python")
    payload["day_boundary"] = {
        "start_place": {
            "place_id": "airport",
            "name": "제주국제공항",
            "coordinates": {"latitude": 33.51, "longitude": 126.49},
        },
        "end_place": {
            "place_id": "port",
            "name": "제주항",
            "coordinates": {"latitude": 33.52, "longitude": 126.54},
        },
    }
    payload["place_duration_preferences"] = [
        {"place_id": "required", "requested_stay_minutes": 90, **metadata}
    ]
    request = RecommendDayTripsInput.model_validate(payload)

    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success", response.failure
    for recommendation in response.recommendations:
        assert recommendation.start_place_id == "airport"
        assert recommendation.end_place_id == "port"
        requested_visit = next(
            event
            for event in recommendation.timeline
            if event.place_id == "required" and event.type == "visit"
        )
        assert requested_visit.duration_minutes == 90
        first_transfer = next(
            event.transfer for event in recommendation.timeline if event.transfer is not None
        )
        last_transfer = next(
            event.transfer
            for event in reversed(recommendation.timeline)
            if event.transfer is not None
        )
        assert first_transfer.direct_walk is not None
        assert last_transfer.direct_walk is not None
        assert first_transfer.direct_walk.from_id == "airport"
        assert last_transfer.direct_walk.to_id == "port"


def test_completed_rest_does_not_count_toward_the_next_continuous_activity_period() -> None:
    """휴식 장소로 이동한 후 완료한 휴식 시간은 다음 연속 활동 시간에 포함하지 않아야 한다."""

    class LongerRouteToRestGateway(FixedGenerationGateway):
        """휴식 직전 이동이 긴 경로를 제공한다."""

        def route(self, from_id, to_id, departure_at, strategy, request, budget):
            route = super().route(from_id, to_id, departure_at, strategy, request, budget)
            return replace(route, duration_minutes=65) if to_id == "rest" else route

    response = DeterministicDayTripGenerator(
        LongerRouteToRestGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success", response.failure


def test_generator_keeps_soft_continuous_activity_limit_as_a_caution() -> None:
    """필수 휴식 편의가 없으면 연속 활동 초과는 추천 생성 실패가 아닌 평가 경고로 남겨야 한다."""

    class VisitOnlyProposer:
        """자동 식사·카페를 끈 요청에 서로 다른 관광 순서 세 개를 제공한다."""

        def propose(self, request, places):
            return {
                Strategy.BALANCED: ("required", "a", "b"),
                Strategy.RELAXED: ("required", "b"),
                Strategy.EXPERIENCE_MAX: ("a", "required", "b"),
            }

    base = _request()
    request = base.model_copy(
        update={
            "food": base.food.model_copy(
                update={"auto_schedule_meals": False, "auto_schedule_cafe": False}
            )
        }
    )
    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        VisitOnlyProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success", response.failure
    assert len(response.recommendations) == 3


def test_dynamic_primary_skeletons_share_routes_within_taxi_allocation() -> None:
    """전략별 primary 뼈대는 택시 열 번 안에 검증되도록 공통 경로를 최대한 재사용해야 한다."""

    gateway = FixedGenerationGateway()
    places = (
        tuple(gateway._places.values())
        + tuple(
            replace(
                gateway._places["a"],
                place_id=f"extra-{index}",
                position=Coordinates(latitude=33.50 + index / 100, longitude=126.75),
            )
            for index in range(4)
        )
        + (
            replace(gateway._places["meal"], place_id="meal-2"),
            replace(gateway._places["rest"], place_id="rest-2"),
        )
    )

    orders = DynamicClusterCandidateAssembler().propose(_request(), places)
    unique_edges = {
        edge
        for order in orders.values()
        for edge in zip(("hotel", *order), (*order, "hotel"), strict=True)
    }

    assert len(unique_edges) <= 10


def test_generator_returns_three_verified_diverse_strategies() -> None:
    """생성기는 모든 시각을 route fact로 계산해 서로 다른 세 전략만 성공 반환해야 한다."""

    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=datetime(2026, 8, 10, 10, tzinfo=KST))
    assert response.status == "success"
    assert {item.strategy for item in response.recommendations} == set(Strategy)
    assert all("required" in item.place_ids for item in response.recommendations)
    assert all(item.timeline[-1].type == "transfer" for item in response.recommendations)


def test_generator_schedules_independent_strategies_concurrently() -> None:
    """세 전략의 첫 경로 계산은 전역 실행예산 안에서 서로 독립적으로 시작해야 한다."""

    class ConcurrentGateway(FixedGenerationGateway):
        def __init__(self) -> None:
            super().__init__()
            self._first_routes = Barrier(3)

        def route(self, from_id, to_id, departure_at, strategy, request, budget):
            if from_id == "hotel":
                self._first_routes.wait(timeout=1)
            return super().route(from_id, to_id, departure_at, strategy, request, budget)

    response = DeterministicDayTripGenerator(
        ConcurrentGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success"
    assert len(response.recommendations) == 3


def test_generator_keeps_unknown_opening_hours_explicit() -> None:
    """운영시간이 없으면 시각을 만들지 않고 미확인 활동과 전역 경고로 생성해야 한다."""

    gateway = FixedGenerationGateway()
    gateway._places = {
        place_id: replace(
            place,
            opens_at=None,
            closes_at=None,
            last_admission_at=None,
            last_order_at=None,
            operating_hours_status="UNVERIFIED",
        )
        for place_id, place in gateway._places.items()
    }
    response = DeterministicDayTripGenerator(
        gateway,
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success"
    assert "OPENING_HOURS_UNKNOWN" in response.global_warnings
    for recommendation in response.recommendations:
        for event in recommendation.timeline:
            details = event.visit or event.meal or event.rest
            if details is None:
                continue
            assert details.operating_hours_status == "UNVERIFIED"
            assert details.opens_at is None
            assert details.closes_at is None


def test_generator_does_not_insert_opening_wait_before_already_open_places() -> None:
    """이미 운영 중인 장소 앞에는 전략 여유와 별개의 운영 대기를 삽입하지 않아야 한다."""

    request = _request().model_copy(
        update={
            "activity_window": _request().activity_window.model_copy(
                update={"start_at": datetime(2026, 8, 15, 10, tzinfo=KST)}
            )
        }
    )

    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success"
    assert all(
        event.reason_code != "OPERATING_OR_MEAL_WINDOW_WAIT"
        for recommendation in response.recommendations
        for event in recommendation.timeline
        if event.type == "buffer"
    )


def test_generator_waits_until_next_open_period_after_verified_break() -> None:
    """검증 휴게 중 도착하면 해당 구간을 통과시키지 않고 다음 OPEN까지 기다려야 한다."""

    day = datetime(2026, 8, 15, tzinfo=KST)
    place = replace(
        FixedGenerationGateway()._places["a"],
        opening_windows=(
            VerifiedOpeningWindow(
                opens_at=day.replace(hour=9),
                closes_at=day.replace(hour=12),
                evidence_fact_ids=("open-morning", "break-noon"),
            ),
            VerifiedOpeningWindow(
                opens_at=day.replace(hour=13),
                closes_at=day.replace(hour=18),
                evidence_fact_ids=("open-afternoon", "break-noon"),
            ),
        ),
    )

    selected, ready_at = DeterministicDayTripGenerator._select_opening_window(
        place, day.replace(hour=12, minute=30)
    )

    assert selected is not None
    assert selected.opens_at == day.replace(hour=13)
    assert ready_at == day.replace(hour=13)


def test_generator_applies_strategy_transfer_slack_and_reports_matching_risk() -> None:
    """전략별 이동 여유 버퍼는 정책 임계값을 사용하고 추천 위험도와 일치해야 한다."""

    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success", response.failure
    recommendations = {item.strategy: item for item in response.recommendations}
    expected = {
        Strategy.BALANCED: (10, "medium"),
        Strategy.RELAXED: (20, "low"),
        Strategy.EXPERIENCE_MAX: (0, "high"),
    }
    for strategy, (minutes, risk) in expected.items():
        recommendation = recommendations[strategy]
        buffers = tuple(
            event
            for event in recommendation.timeline
            if event.type == "buffer" and event.reason_code == "PLANNED_SAFETY_BUFFER"
        )
        assert len(buffers) == (len(recommendation.place_ids) if minutes else 0)
        assert all(event.duration_minutes == minutes for event in buffers)
        assert recommendation.overall_risk == risk


def test_generator_separates_cost_balance_taxi_pickup_from_driving() -> None:
    """cost-time-balance 택시는 생성 타임라인에서 호출 대기와 실제 주행을 분리해야 한다."""

    class TaxiGateway(FixedGenerationGateway):
        def route(self, from_id, to_id, departure_at, strategy, request, budget):
            budget.claim_external_call("fixture-taxi-routing")
            fact_id = f"fact-route-{from_id}-{to_id}"
            taxi = TaxiAlternative(
                duration_minutes=10,
                distance_meters=5_000,
                fare_min_krw=7_000,
                fare_max_krw=9_000,
                evidence_fact_ids=(fact_id,),
            )
            decision = ModeDecision(
                policy_id="cost-time-balance-v1",
                selected_mode="taxi",
                origin_basis=EndpointBasis.VERIFIED_ENTRANCE,
                destination_basis=EndpointBasis.VERIFIED_ENTRANCE,
                taxi_pickup_buffer_minutes=10,
                taxi_driving_minutes=10,
                taxi_door_to_door_minutes=20,
                reason_codes=("TAXI_PICKUP_PLANNING_BUFFER_APPLIED",),
                evidence_fact_ids=(fact_id,),
            )
            return VerifiedRouteOption(
                from_id,
                to_id,
                20,
                0,
                0,
                7_000,
                9_000,
                0,
                Transfer(
                    mode="taxi",
                    taxi_alternative=taxi,
                    distance_meters=5_000,
                    mode_decision=decision,
                ),
                (fact_id,),
            )

    request = _request().model_copy(
        update={
            "transport": _request().transport.model_copy(
                update={"selection_policy": "cost_time_balance"}
            )
        }
    )
    response = DeterministicDayTripGenerator(
        TaxiGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))

    assert response.status == "success"
    for recommendation in response.recommendations:
        for index, event in enumerate(recommendation.timeline):
            if event.type == "transfer":
                previous = recommendation.timeline[index - 1]
                assert previous.type == "buffer"
                assert previous.reason_code == "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
                assert previous.duration_minutes == 10
                assert event.duration_minutes == 10


class DuplicateOrderProposer(FixedOrderProposer):
    """세 전략에 같은 순서만 제안한다."""

    def propose(self, request, places):
        return {strategy: ("required", "meal", "a", "rest") for strategy in Strategy}


def test_generator_returns_no_partial_routes_when_diversity_fails() -> None:
    """세 전략이 실질적으로 같으면 일부 추천 없이 전체 실패해야 한다."""

    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        DuplicateOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(_request(), now=datetime(2026, 8, 10, 10, tzinfo=KST))
    assert response.status == "insufficient_feasible_routes"
    assert response.recommendations == ()
    assert response.failure is not None
    assert "ROUTES_NOT_DIVERSE" in response.failure.reason_codes


def test_generator_resolves_name_only_hotel_and_places_before_scheduling() -> None:
    """이름만 받은 숙소·장소는 gateway가 유일하게 확정한 ID로 정규화한 뒤 생성해야 한다."""

    class ResolvingGateway(FixedGenerationGateway):
        def resolve_request(self, request):
            payload = request.model_dump(mode="python")
            payload["accommodation"] = {
                "place_id": "hotel",
                "name": "제주 숙소",
                "coordinates": {"latitude": 33.49, "longitude": 126.5},
            }
            payload["required_places"] = ({"place_id": "required", "name": "필수"},)
            payload["preferred_places"] = (
                {"place_id": "a", "name": "선호 A"},
                {"place_id": "b", "name": "선호 B"},
            )
            return RecommendDayTripsInput.model_validate(payload)

    request = _request().model_copy(
        update={
            "accommodation": _request().accommodation.model_copy(update={"place_id": None}),
            "required_places": (
                _request().required_places[0].model_copy(update={"place_id": None}),
            ),
        }
    )
    response = DeterministicDayTripGenerator(
        ResolvingGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 10, 10, tzinfo=KST))
    assert response.status == "success"
    assert response.request.accommodation.place_id == "hotel"
    assert response.request.required_places[0].place_id == "required"
