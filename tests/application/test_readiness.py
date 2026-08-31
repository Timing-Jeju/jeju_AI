"""기능별 capability readiness gate 테스트."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from jeju_trip.application.readiness import CapabilityReadiness, PostgresCapabilityReadiness
from jeju_trip.application.service import TripPlannerService
from jeju_trip.domain.models import (
    AccommodationInput,
    ActivitiesOnlyEvaluationInput,
    ActivityWindow,
    Coordinates,
    PlaceReference,
    PreviewTransferInput,
    ScheduledActivity,
)
from tests.factories import make_request

KST = timezone(timedelta(hours=9))


def _bus_only_request():
    request = make_request()
    return request.model_copy(
        update={
            "transport": request.transport.model_copy(
                update={
                    "allowed_modes": {"bus"},
                    "preferred_mode": "bus",
                    "fallback_order": (),
                }
            )
        }
    )


def _taxi_only_request():
    request = make_request()
    return request.model_copy(
        update={
            "transport": request.transport.model_copy(
                update={
                    "allowed_modes": {"taxi"},
                    "preferred_mode": "taxi",
                    "fallback_order": (),
                }
            )
        }
    )


def _evaluation_request() -> ActivitiesOnlyEvaluationInput:
    return ActivitiesOnlyEvaluationInput(
        trip_date=date(2026, 8, 15),
        schedule_format="activities_only",
        accommodation=AccommodationInput(
            place_id="hotel-1",
            name="제주 숙소",
            coordinates=Coordinates(latitude=33.49, longitude=126.49),
        ),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 20, tzinfo=KST),
        ),
        scheduled_activities=(
            ScheduledActivity(
                event_id="visit-1",
                type="visit",
                place=PlaceReference(place_id="place-1", name="첫 장소"),
                start_at=datetime(2026, 8, 15, 10, tzinfo=KST),
                end_at=datetime(2026, 8, 15, 11, tzinfo=KST),
            ),
        ),
    )


def test_bus_generation_requires_future_timetable_and_walking() -> None:
    """버스 생성은 장소·운영시간뿐 아니라 미래 시간표와 부속 도보 준비가 필요하다."""

    readiness = CapabilityReadiness(
        {
            "service_area_ready": True,
            "place_search_ready": True,
            "opening_hours_ready": True,
            "walking_routing_ready": True,
            "future_bus_planning_ready": False,
            "restaurant_recommendation_ready": True,
            "cafe_recommendation_ready": True,
            "accessibility_ready": True,
        }
    )
    result = readiness.for_generation(_bus_only_request())
    assert result.ready is False
    assert "future_bus_planning_ready" in result.missing_capabilities


def test_generation_requires_polygon_backed_service_area_coverage() -> None:
    """사각형 좌표 prefilter만으로는 제주 전역 지원을 켜지 않아야 한다."""

    flags = {
        "place_search_ready": True,
        "opening_hours_ready": True,
        "walking_routing_ready": True,
        "future_bus_planning_ready": True,
    }

    result = CapabilityReadiness(flags).for_generation(make_request())

    assert result.ready is False
    assert "service_area_ready" in result.missing_capabilities


def test_generation_does_not_require_place_price_coverage() -> None:
    """장소 가격을 제공하지 않는 일정도 이동비와 운영시간 근거만 있으면 준비돼야 한다."""

    flags = {
        "service_area_ready": True,
        "place_search_ready": True,
        "opening_hours_ready": True,
        "verified_entrances_ready": True,
        "walking_routing_ready": True,
        "driving_routing_ready": True,
        "future_bus_planning_ready": True,
        "confirmed_stop_mapping_ready": True,
        "fare_policy_ready": True,
    }

    result = CapabilityReadiness(flags).for_generation(make_request())

    assert result.ready is True
    assert "place_pricing_ready" not in result.missing_capabilities


def test_generation_reports_unknown_opening_hours_without_blocking() -> None:
    """운영시간 미확인은 일반 일정 생성을 막지 않고 결과에서 주의 상태로 남겨야 한다."""

    flags = {
        "service_area_ready": True,
        "place_search_ready": True,
        "opening_hours_ready": False,
        "walking_routing_ready": True,
        "driving_routing_ready": True,
        "future_bus_planning_ready": True,
        "confirmed_stop_mapping_ready": True,
        "fare_policy_ready": True,
    }

    result = CapabilityReadiness(flags).for_generation(make_request())

    assert result.ready is True
    assert "opening_hours_ready" not in result.missing_capabilities


def test_bus_only_generation_ignores_stale_taxi_fare_policy() -> None:
    """버스 전용 요청은 버스 요금이 fresh하면 stale 택시 요금 때문에 닫히지 않아야 한다."""

    result = CapabilityReadiness(
        {
            "service_area_ready": True,
            "place_search_ready": True,
            "walking_routing_ready": True,
            "future_bus_planning_ready": True,
            "confirmed_stop_mapping_ready": True,
            "bus_fare_policy_ready": True,
            "taxi_fare_policy_ready": False,
        }
    ).for_generation(_bus_only_request())

    assert result.ready is True


def test_taxi_only_generation_ignores_stale_bus_fare_policy() -> None:
    """택시 전용 요청은 택시 요금이 fresh하면 stale 버스 요금 때문에 닫히지 않아야 한다."""

    result = CapabilityReadiness(
        {
            "service_area_ready": True,
            "place_search_ready": True,
            "driving_routing_ready": True,
            "bus_fare_policy_ready": False,
            "taxi_fare_policy_ready": True,
        }
    ).for_generation(_taxi_only_request())

    assert result.ready is True


def test_multi_mode_generation_requires_every_allowed_mode_to_be_grounded() -> None:
    """허용 수단 중 하나라도 stale이면 planner가 시도하지 않도록 요청을 닫아야 한다."""

    result = CapabilityReadiness(
        {
            "service_area_ready": True,
            "place_search_ready": True,
            "walking_routing_ready": True,
            "future_bus_planning_ready": False,
            "confirmed_stop_mapping_ready": True,
            "bus_fare_policy_ready": True,
            "driving_routing_ready": True,
            "taxi_fare_policy_ready": True,
        }
    ).for_generation(make_request())

    assert result.ready is False
    assert "future_bus_planning_ready" in result.missing_capabilities


def test_accessibility_requirement_never_uses_unverified_default_entrance() -> None:
    """이동보조 필수 요청은 accessibility capability가 없으면 생성하지 않아야 한다."""

    request = make_request().model_copy(
        update={
            "party": make_request().party.model_copy(update={"mobility_support_required": True})
        }
    )
    readiness = CapabilityReadiness(
        {
            "place_search_ready": True,
            "opening_hours_ready": True,
            "walking_routing_ready": True,
            "driving_routing_ready": True,
            "future_bus_planning_ready": True,
            "restaurant_recommendation_ready": True,
            "cafe_recommendation_ready": True,
            "accessibility_ready": False,
        }
    )
    result = readiness.for_generation(request)
    assert result.ready is False
    assert "accessibility_ready" in result.missing_capabilities


def test_realtime_bus_requires_separate_realtime_capability() -> None:
    """정적 버스 계획 준비만으로 실시간 버스 정상 판정을 해서는 안 된다."""

    readiness = CapabilityReadiness(
        {"future_bus_planning_ready": True, "realtime_bus_ready": False}
    )
    result = readiness.for_realtime(uses_bus=True)
    assert result.ready is False
    assert result.missing_capabilities == ("realtime_bus_ready",)


def test_realtime_readiness_uses_static_mapping_and_ephemeral_adapter() -> None:
    """저장하지 않는 실시간 응답은 정적 버스 coverage와 연결된 adapter로 준비 판정해야 한다."""

    class Repository:
        def capability_flags(self, trip_date):
            return {"confirmed_stop_mapping_ready": True}

    readiness = PostgresCapabilityReadiness(
        Repository(),  # type: ignore[arg-type]
        {"realtime_bus_ready": True},
    )
    result = readiness.for_realtime(uses_bus=True, trip_date=date(2026, 8, 15))
    assert result.ready is True


def test_future_bus_runtime_adapter_cannot_replace_missing_publication() -> None:
    """TMAP adapter 연결만으로 공식 미래 버스 시간표 readiness를 켜지 않아야 한다."""

    class Repository:
        def capability_flags(self, trip_date):
            return {
                "service_area_ready": True,
                "place_search_ready": True,
                "opening_hours_ready": True,
            }

    readiness = PostgresCapabilityReadiness(
        Repository(),  # type: ignore[arg-type]
        {"walking_routing_ready": True, "future_bus_planning_ready": True},
    )

    result = readiness.for_generation(_bus_only_request())

    assert result.ready is False
    assert "future_bus_planning_ready" in result.missing_capabilities


def test_request_exact_bus_service_can_open_partial_global_coverage() -> None:
    """전역 coverage가 미완료여도 요청 장소의 exact 운행편은 버스 생성을 열어야 한다."""

    class Repository:
        def capability_states(self, trip_date, region_code, grid_id):
            return {
                "service_area_ready": SimpleNamespace(ready=True, reason="READY"),
                "place_search_ready": SimpleNamespace(ready=True, reason="READY"),
                "future_bus_planning_ready": SimpleNamespace(
                    ready=False, reason="COVERAGE_INCOMPLETE"
                ),
                "confirmed_stop_mapping_ready": SimpleNamespace(ready=True, reason="READY"),
                "bus_fare_policy_ready": SimpleNamespace(ready=True, reason="READY"),
            }

        def exact_bus_planning_available(self, request):
            return True

    readiness = PostgresCapabilityReadiness(
        Repository(),  # type: ignore[arg-type]
        {"walking_routing_ready": True},
    )

    result = readiness.for_generation(_bus_only_request())

    assert result.ready is True
    assert "future_bus_planning_ready" not in result.missing_capabilities


def test_exact_bus_timeline_can_open_evaluation_under_partial_global_coverage() -> None:
    """전역 coverage가 미완료여도 일정 구간의 exact 운행편은 버스 판정을 열어야 한다."""

    class Repository:
        def capability_states(self, trip_date, region_code, grid_id):
            return {
                "service_area_ready": SimpleNamespace(ready=True, reason="READY"),
                "place_search_ready": SimpleNamespace(ready=True, reason="READY"),
                "future_bus_planning_ready": SimpleNamespace(
                    ready=False, reason="COVERAGE_INCOMPLETE"
                ),
                "confirmed_stop_mapping_ready": SimpleNamespace(ready=True, reason="READY"),
                "bus_fare_policy_ready": SimpleNamespace(ready=True, reason="READY"),
                "taxi_fare_policy_ready": SimpleNamespace(ready=True, reason="READY"),
            }

        def exact_bus_evaluation_available(self, request):
            return True

    readiness = PostgresCapabilityReadiness(
        Repository(),  # type: ignore[arg-type]
        {"walking_routing_ready": True, "driving_routing_ready": True},
    )

    result = readiness.for_evaluation(_evaluation_request())

    assert result.ready is True
    assert "future_bus_planning_ready" not in result.missing_capabilities


def test_request_exact_bus_service_cannot_reopen_stale_timetable() -> None:
    """시간표가 stale로 닫혔으면 exact 운행편 행이 남아 있어도 버스 생성을 열지 않아야 한다."""

    probe_called = False

    class Repository:
        def capability_states(self, trip_date, region_code, grid_id):
            return {
                "service_area_ready": SimpleNamespace(ready=True, reason="READY"),
                "place_search_ready": SimpleNamespace(ready=True, reason="READY"),
                "future_bus_planning_ready": SimpleNamespace(ready=False, reason="STALE"),
                "confirmed_stop_mapping_ready": SimpleNamespace(ready=True, reason="READY"),
                "bus_fare_policy_ready": SimpleNamespace(ready=True, reason="READY"),
            }

        def exact_bus_planning_available(self, request):
            nonlocal probe_called
            probe_called = True
            return True

    readiness = PostgresCapabilityReadiness(
        Repository(),  # type: ignore[arg-type]
        {"walking_routing_ready": True},
    )

    result = readiness.for_generation(_bus_only_request())

    assert result.ready is False
    assert "future_bus_planning_ready" in result.missing_capabilities
    assert probe_called is False


def test_request_exact_bus_service_reopens_only_incomplete_coverage() -> None:
    """요청 단위 exact 운행편은 fresh하지만 전역 coverage만 불완전한 경우에만 버스를 연다."""

    class Repository:
        def capability_states(self, trip_date, region_code, grid_id):
            return {
                "service_area_ready": SimpleNamespace(ready=True, reason="READY"),
                "place_search_ready": SimpleNamespace(ready=True, reason="READY"),
                "future_bus_planning_ready": SimpleNamespace(
                    ready=False, reason="COVERAGE_INCOMPLETE"
                ),
                "confirmed_stop_mapping_ready": SimpleNamespace(ready=True, reason="READY"),
                "bus_fare_policy_ready": SimpleNamespace(ready=True, reason="READY"),
            }

        def exact_bus_planning_available(self, request):
            return True

    readiness = PostgresCapabilityReadiness(
        Repository(),  # type: ignore[arg-type]
        {"walking_routing_ready": True},
    )

    result = readiness.for_generation(_bus_only_request())

    assert result.ready is True


def test_missing_verified_entrances_do_not_block_standard_generation() -> None:
    """일반 요청은 검증 입구가 없어도 장소 대표좌표로 경로 생성을 계속해야 한다."""

    base = make_request()
    request = base.model_copy(
        update={
            "transport": base.transport.model_copy(
                update={
                    "allowed_modes": {"walk"},
                    "preferred_mode": "walk",
                    "fallback_order": (),
                }
            )
        }
    )

    class Repository:
        def capability_flags(self, trip_date):
            return {
                "service_area_ready": True,
                "place_search_ready": True,
                "opening_hours_ready": True,
                "future_bus_planning_ready": True,
            }

    readiness = PostgresCapabilityReadiness(
        Repository(),  # type: ignore[arg-type]
        {
            "walking_routing_ready": True,
            "driving_routing_ready": True,
            "future_bus_planning_ready": True,
        },
    )

    result = readiness.for_generation(request)

    assert result.ready is True
    assert "verified_entrances_ready" not in result.missing_capabilities


def test_accessibility_still_requires_verified_entrances() -> None:
    """이동보조 필수 요청은 대표좌표 fallback 대신 검증 입구 coverage를 계속 요구해야 한다."""

    base = make_request()
    request = base.model_copy(
        update={"party": base.party.model_copy(update={"mobility_support_required": True})}
    )
    flags = {
        "service_area_ready": True,
        "place_search_ready": True,
        "opening_hours_ready": True,
        "walking_routing_ready": True,
        "driving_routing_ready": True,
        "future_bus_planning_ready": True,
        "confirmed_stop_mapping_ready": True,
        "fare_policy_ready": True,
        "accessibility_ready": True,
    }

    result = CapabilityReadiness(flags).for_generation(request)

    assert result.ready is False
    assert result.missing_capabilities == ("verified_entrances_ready",)


def test_dietary_constraints_require_verified_restaurant_capability() -> None:
    """알레르기·제외음식이 있는 자동 식사는 검증된 식당 capability 없이 생성하면 안 된다."""

    base = make_request()
    request = base.model_copy(
        update={"food": base.food.model_copy(update={"allergens": ("땅콩",)})}
    )
    flags = {
        "place_search_ready": True,
        "opening_hours_ready": True,
        "walking_routing_ready": True,
        "future_bus_planning_ready": True,
        "restaurant_recommendation_ready": False,
    }

    result = CapabilityReadiness(flags).for_generation(request)

    assert result.ready is False
    assert "restaurant_recommendation_ready" in result.missing_capabilities


def test_service_stops_before_generator_when_capability_is_missing() -> None:
    """서비스는 필수 capability가 꺼져 있으면 생성기를 호출하지 않고 구조화 실패해야 한다."""

    class ExplodingGenerator:
        def generate(self, request):
            raise AssertionError("생성기가 호출되면 안 됩니다.")

    response = TripPlannerService(
        generator=ExplodingGenerator(),  # type: ignore[arg-type]
        readiness=CapabilityReadiness({}),
    ).recommend(make_request())
    assert response.status == "insufficient_feasible_routes"
    assert response.failure is not None
    assert response.failure.code == "DATA_NOT_READY"
    assert "place_search_ready" in response.failure.missing_capabilities


def test_service_does_not_build_evaluation_evidence_when_capability_is_missing() -> None:
    """판정 source가 준비되지 않았으면 runtime evidence factory를 호출하지 않아야 한다."""

    def exploding_factory(request):
        raise AssertionError("stale 판정 근거 factory가 호출되면 안 됩니다.")

    response = TripPlannerService(
        readiness=CapabilityReadiness({}),
        evaluation_evidence_factory=exploding_factory,
    ).evaluate(_evaluation_request())

    assert response.status == "unverifiable"
    assert response.evidence_status == "unavailable"


def test_service_stops_transfer_preview_when_capability_is_missing() -> None:
    """이동 미리보기는 필요한 source readiness가 닫히면 gateway 경로를 호출하지 않아야 한다."""

    class ExplodingGateway:
        def preview_transfer(self, request):
            raise AssertionError("stale 미리보기 gateway가 호출되면 안 됩니다.")

    response = TripPlannerService(
        gateway=ExplodingGateway(),  # type: ignore[arg-type]
        readiness=CapabilityReadiness({}),
    ).preview_transfer(
        PreviewTransferInput(
            origin_place_id="place-a",
            destination_place_id="place-b",
            departure_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            allowed_modes={"bus"},
        )
    )

    assert response.status == "unavailable"
    assert response.reason_code == "REQUIRED_CAPABILITY_UNAVAILABLE"


def test_party_size_does_not_require_separate_pricing_capability() -> None:
    """인원 구성은 공식 요금 계산에만 쓰고 별도 pricing capability로 생성을 막지 않아야 한다."""

    base = make_request()
    request = base.model_copy(update={"party": base.party.model_copy(update={"adults": 2})})
    flags = {
        "service_area_ready": True,
        "place_search_ready": True,
        "opening_hours_ready": True,
        "verified_entrances_ready": True,
        "walking_routing_ready": True,
        "driving_routing_ready": True,
        "future_bus_planning_ready": True,
        "confirmed_stop_mapping_ready": True,
        "fare_policy_ready": True,
    }

    result = CapabilityReadiness(flags).for_generation(request)

    assert result.ready is True
    assert "party_pricing_ready" not in result.missing_capabilities
