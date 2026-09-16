"""Postgres 생성 gateway의 공식 장소 분류 테스트."""

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest

from jeju_trip.domain.models import Coordinates, EndpointBasis, RecommendDayTripsInput, Strategy
from jeju_trip.infrastructure.runtime_generation_gateway import (
    PostgresGenerationGateway,
    _BusCandidateLeg,
    _verified_opening_windows,
)
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.planning.generation import VerifiedGenerationPlace
from jeju_trip.planning.policy import load_planning_policy
from tests.factories import make_request

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))


def test_verified_entrance_fact_preserves_canonical_place_binding(monkeypatch) -> None:
    """검증 입구 근거는 좌표 없이 canonical 장소와 입구의 연결을 함께 전달해야 한다."""

    gateway = object.__new__(PostgresGenerationGateway)
    gateway._facts = {}
    gateway._entrance_rows = {
        "tourapi.place:123": (
            ("entrance-fact-123", "entrance-123", 33.45, 126.7,
             ["walk", "bus"], "publication-123", date(2026, 8, 1)),
        )
    }
    observed_sources = []
    monkeypatch.setattr(
        gateway, "_add_source_metadata",
        lambda source_id, publication_id: observed_sources.append((source_id, publication_id)),
    )

    endpoints = gateway._load_entrances("tourapi.place:123", make_request())

    assert endpoints[0].place_id == "tourapi.place:123"
    fact = gateway._facts["entrance-fact-123"]
    assert fact.value == {"entrance_id": "entrance-123", "place_id": "tourapi.place:123"}
    assert fact.derivation.kind == "source"
    assert fact.source_refs[0].source_id == "travel.place-entrance-map"
    assert observed_sources == [("travel.place-entrance-map", "publication-123")]


def test_official_cafe_category_uses_cafe_stay_policy() -> None:
    """TourAPI 카페/전통찻집 코드는 이름과 무관하게 카페 체류 정책을 선택해야 한다."""

    assert PostgresGenerationGateway._stay_policy_key("39", "A05020900") == "cafe"
    assert PostgresGenerationGateway._stay_policy_key("39", "A05020100") == "restaurant"


def test_dietary_constrained_generation_drops_meal_without_exact_safe_fact() -> None:
    """알레르기·제외음식 요청은 exact 안전 메뉴 fact가 없는 식당을 생성 후보에서 빼야 한다."""

    gateway = object.__new__(PostgresGenerationGateway)
    gateway._clustered_candidate_ids = lambda request: ("tourapi.place:meal",)
    gateway._prime_place_candidates = lambda place_ids, request: None
    gateway._load_entrances = lambda place_id, request: ()
    gateway._load_place = lambda place_id, request: VerifiedGenerationPlace(
        place_id=place_id,
        name="식당",
        position=Coordinates(latitude=33.5, longitude=126.5),
        entrance_id="place-point:meal",
        category="39",
        opens_at=None,
        closes_at=None,
        last_admission_at=None,
        stay_minutes=60,
        is_estimated_stay=True,
        evidence_fact_ids=(place_id, "policy:stay"),
        operating_hours_status="UNVERIFIED",
        activity_type="meal",
    )
    def no_safe_fact(
        place_id: str,
        allergens: tuple[str, ...],
        excluded_foods: tuple[str, ...],
        on_date: date,
    ) -> tuple[bool, tuple[str, ...]] | None:
        return None

    gateway.dietary_safety = no_safe_fact
    base = make_request()
    request = base.model_copy(
        update={"food": base.food.model_copy(update={"allergens": ("땅콩",)})}
    )

    assert gateway.places(request) == ()


def test_dietary_safety_uses_exact_normalized_array_containment(monkeypatch) -> None:
    """식이 조회는 fuzzy 확장 없이 정규화된 exact 배열 포함으로만 확인해야 한다."""

    captured: dict[str, object] = {}

    class Result:
        def fetchone(self):
            return (
                "dietary-fact-1",
                "menu-1",
                "검증 메뉴",
                ["peanut"],
                ["돼지고기"],
                "publication-1",
                "travel.restaurant-dietary-map",
                date(2026, 8, 30),
                datetime(2026, 8, 30, tzinfo=UTC),
            )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            captured["statement"] = str(statement)
            captured["parameters"] = parameters
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_generation_gateway.psycopg.connect",
        lambda dsn: Connection(),
    )
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._runtime_dsn = "postgresql://runtime"
    gateway._source_is_fresh = lambda *args, **kwargs: True
    gateway._add_source_fact = lambda *args, **kwargs: None

    result = gateway.dietary_safety(
        "tourapi.place:1",
        (" Peanut ",),
        ("돼지고기",),
        date(2026, 8, 31),
    )

    assert result == (True, ("dietary-fact-1",))
    assert "@> %s::text[]" in str(captured["statement"])
    assert captured["parameters"] == (
        "tourapi.place:1",
        ["peanut"],
        ["돼지고기"],
        date(2026, 8, 31),
        date(2026, 8, 31),
        date(2026, 8, 31),
    )


def test_relaxed_bus_order_keeps_balanced_prefix_and_removes_late_visit() -> None:
    """relaxed 버스 순서는 balanced 앞 관광 둘을 보존하고 후반 관광만 제거해야 한다."""

    places = {
        "visit-a": SimpleNamespace(activity_type="visit"),
        "visit-b": SimpleNamespace(activity_type="visit"),
        "meal": SimpleNamespace(activity_type="meal"),
        "visit-c": SimpleNamespace(activity_type="visit"),
        "rest": SimpleNamespace(activity_type="rest"),
    }

    order = PostgresGenerationGateway._relaxed_prefix_order(
        ("visit-a", "visit-b", "meal", "visit-c", "rest"),
        cast(dict[str, VerifiedGenerationPlace], places),
        set(),
    )

    assert order == ("visit-a", "visit-b", "meal", "rest")


def test_place_and_opening_freshness_uses_declared_temporal_basis() -> None:
    """시각 근거가 없을 때는 승인된 source date만 신선도 근거로 사용해야 한다."""

    assert not PostgresGenerationGateway._fresh_enough(None, 8)
    assert not PostgresGenerationGateway._fresh_enough(datetime.now(UTC) - timedelta(days=9), 8)
    assert not PostgresGenerationGateway._fresh_enough(datetime.now(UTC) + timedelta(minutes=10), 8)
    assert PostgresGenerationGateway._fresh_enough(datetime.now(UTC) - timedelta(days=1), 8)
    assert PostgresGenerationGateway._fresh_enough(None, 8, source_date=date.today())
    assert not PostgresGenerationGateway._fresh_enough(
        None,
        8,
        source_date=date.today() - timedelta(days=9),
    )
    assert not PostgresGenerationGateway._fresh_enough(
        None,
        8,
        source_date=date.today() + timedelta(days=1),
    )


def test_verified_opening_windows_keep_all_periods_and_subtract_breaks() -> None:
    """생성 후보의 복수 OPEN 구간은 휴게시간을 빼고 각 경계 근거를 보존해야 한다."""

    day_start = datetime(2026, 8, 31, tzinfo=KST)
    rows = (
        (
            "open-fact",
            "publication",
            9 * 60,
            18 * 60,
            0,
            17 * 60,
            None,
            (),
            "tourapi.place-intro",
            date(2026, 8, 30),
            datetime(2026, 8, 30, tzinfo=UTC),
            "OPEN",
        ),
        (
            "break-fact",
            "publication",
            12 * 60,
            13 * 60,
            0,
            None,
            None,
            (),
            "tourapi.place-intro",
            date(2026, 8, 30),
            datetime(2026, 8, 30, tzinfo=UTC),
            "BREAK",
        ),
    )

    windows = _verified_opening_windows(rows, day_start)

    assert tuple((item.opens_at.hour, item.closes_at.hour) for item in windows) == (
        (9, 12),
        (13, 18),
    )
    assert all(item.evidence_fact_ids == ("open-fact", "break-fact") for item in windows)
    assert all(item.last_admission_at == day_start.replace(hour=17) for item in windows)


def test_stale_active_source_metadata_is_not_reported_as_active(monkeypatch) -> None:
    """활성 snapshot도 source freshness를 넘기면 공개 metadata에서 STALE로 표시해야 한다."""

    class Result:
        def fetchone(self):
            return (
                "tourapi.place",
                "한국관광공사",
                "2026-08-11-snapshot",
                None,
                datetime(2026, 8, 11, 1, 38, tzinfo=UTC),
                datetime(2026, 8, 11, 1, 39, tzinfo=UTC),
            )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_generation_gateway.psycopg.connect",
        lambda dsn: Connection(),
    )
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._sources = {}
    gateway._runtime_dsn = "postgresql://runtime"
    gateway._source_catalog = SourceCatalog.load(ROOT / "config" / "data_sources.toml")
    gateway._now = lambda: datetime(2026, 8, 30, 0, 0, tzinfo=UTC)

    gateway._add_source_metadata("tourapi.place", None)

    assert gateway.data_sources()[0].status == "STALE"


def test_representative_place_point_supports_standard_transport_modes() -> None:
    """검증 입구가 없으면 장소 fact 좌표를 출처가 표시된 임시 경로 endpoint로 사용해야 한다."""

    endpoint = PostgresGenerationGateway._representative_endpoint(
        place_id="tourapi.place:123",
        position=Coordinates(latitude=33.45, longitude=126.91),
        allowed_modes={"walk", "bus", "taxi"},
    )

    assert endpoint.entrance_id == "place-point:tourapi.place:123"
    assert endpoint.endpoint_basis == EndpointBasis.REPRESENTATIVE_PLACE_POINT
    assert endpoint.supported_modes == ("bus", "taxi", "walk")
    assert endpoint.evidence_fact_ids == ("tourapi.place:123",)


def test_current_gps_endpoint_is_kept_only_in_request_gateway_memory() -> None:
    """현재 GPS는 DB 조회 없이 요청별 gateway 메모리 endpoint로만 등록해야 한다."""

    gateway = object.__new__(PostgresGenerationGateway)
    gateway._entrances = {}
    position = Coordinates(latitude=33.45, longitude=126.91)

    gateway.register_current_endpoint(
        "current:gps",
        position,
        {"walk", "bus", "taxi"},
    )

    endpoint = gateway._entrances["current:gps"][0]
    assert endpoint.position == position
    assert endpoint.endpoint_basis == EndpointBasis.CURRENT_GPS


def test_transfer_preview_falls_back_to_active_place_position(monkeypatch) -> None:
    """이동 미리보기도 검증 입구가 없으면 active 장소 대표좌표 endpoint를 사용해야 한다."""

    gateway = object.__new__(PostgresGenerationGateway)
    monkeypatch.setattr(gateway, "_load_entrances", lambda place_id, request: ())
    monkeypatch.setattr(gateway, "_active_place_is_fresh", lambda place_id: True)
    monkeypatch.setattr(
        gateway,
        "_load_representative_position",
        lambda place_id: Coordinates(latitude=33.45, longitude=126.91),
    )

    endpoints = gateway._load_route_endpoints("tourapi.place:123", make_request())

    assert len(endpoints) == 1
    assert endpoints[0].endpoint_basis == EndpointBasis.REPRESENTATIVE_PLACE_POINT


def test_input_position_outside_active_boundary_is_rejected(monkeypatch) -> None:
    """숙소나 요청 장소 좌표가 활성 제주 경계 밖이면 서비스영역 오류로 거부해야 한다."""

    class Result:
        def fetchone(self):
            return (False,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            assert "ST_Covers" in str(statement)
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_generation_gateway.psycopg.connect",
        lambda dsn: Connection(),
    )
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._runtime_dsn = "postgresql://runtime"

    with pytest.raises(ValueError, match="PLACE_OUTSIDE_SERVICE_AREA"):
        gateway._assert_position_in_service_area(33.0, 125.0)


def test_optional_publication_filter_has_explicit_postgres_text_type(monkeypatch) -> None:
    """source metadata의 선택 publication 파라미터는 PostgreSQL이 추론하도록 두지 않아야 한다."""

    class Result:
        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            assert "%(publication_id)s::text IS NULL" in str(statement)
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_generation_gateway.psycopg.connect",
        lambda dsn: Connection(),
    )
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._runtime_dsn = "postgresql://runtime"
    gateway._sources = {}

    gateway._add_source_metadata("tourapi.place", None)


def test_bus_only_candidates_require_exact_service_day_stops_near_hotel_and_places(
    monkeypatch,
) -> None:
    """버스 전용 후보 모수는 숙소와 장소 양쪽에 당일 exact 정류장이 있어야 한다."""

    class Result:
        def fetchall(self):
            return [("place-bus-reachable",)]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            query = str(statement)
            assert "WITH served_stop AS MATERIALIZED" in query
            assert "active_scheduled_trip" in query
            assert "active_stop_time" in query
            assert "SELECT DISTINCT stop_time.stop_fact_id" in query
            assert "hotel_outbound_stop" not in query
            assert "hotel_return_stop" not in query
            assert "SELECT 1 FROM hotel_stop" in query
            assert "JOIN served_stop served ON served.stop_fact_id = stop.fact_id" in query
            assert query.count("ST_DWithin") >= 2
            assert "active_place_opening_rule opening" in query
            assert "active_source_metadata opening_metadata" in query
            assert "has_applicable_hours" in query
            assert "hours_role_rank" in query
            assert "local_role_rank" in query
            assert "hours_role_rank <= 8 OR local_role_rank <= 8" in query
            assert "hours_role_rank <= 5 OR local_role_rank <= 5" in query
            assert "hours_role_rank <= 3 OR local_role_rank <= 3" in query
            assert "candidate_pool_rank <= 16" in query
            assert "candidate_pool_rank <= 10" in query
            assert "candidate_pool_rank <= 6" in query
            assert "active_place_schedule_exception closure" in query
            assert "active_place_weekly_closure weekly_closure" in query
            assert "candidate.category <> '15' OR candidate.has_applicable_hours" in query
            assert "place.category NOT IN ('32', 'airport')" in query
            assert "CASE WHEN %(bus_only)s" in query
            assert "CASE WHEN NOT %(bus_only)s" in query
            assert query.count("clustered.hotel_distance") >= 2
            assert parameters["bus_only"] is True
            assert parameters["trip_date"] == make_request().trip_date
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_generation_gateway.psycopg.connect",
        lambda dsn: Connection(),
    )
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._runtime_dsn = "postgresql://runtime"
    payload = make_request().model_dump(mode="python")
    payload["accommodation"] = {
        "place_id": "hotel",
        "name": "제주 숙소",
        "coordinates": {"latitude": 33.49, "longitude": 126.5},
    }
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "selection_policy": "cost_time_balance",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
    }
    request = RecommendDayTripsInput.model_validate(payload)

    assert gateway._clustered_candidate_ids(request) == ("place-bus-reachable",)


def test_bus_candidate_graph_includes_exact_one_transfer_paths(monkeypatch) -> None:
    """버스 장소 그래프는 안전시간과 대기한도를 만족한 1회 환승도 포함해야 한다."""

    first_departure = datetime(2026, 8, 15, 9, 10, tzinfo=UTC)
    final_arrival = datetime(2026, 8, 15, 10, 20, tzinfo=UTC)
    queries: list[str] = []

    class Result:
        def __init__(self, rows=(), row=None):
            self._rows = rows
            self._row = row

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            query = str(statement)
            queries.append(query)
            if "active_holiday" in query and "WITH" not in query:
                return Result(row=None)
            if "WITH endpoint AS" in query:
                return Result(
                    rows=(
                        ("hotel", "stop-hotel", 120.0),
                        ("visit", "stop-visit", 180.0),
                    )
                )
            if "0 AS transfers" in query:
                return Result(rows=())
            assert "active_bus_route" in query
            assert "ORDER BY trip.fact_id, stop_time.stop_sequence" in query
            return Result(
                rows=(
                    (
                        "trip-first",
                        "1111",
                        "stop-hotel",
                        1,
                        first_departure,
                        first_departure,
                        33.49,
                        126.5,
                    ),
                    (
                        "trip-first",
                        "1111",
                        "stop-transfer",
                        2,
                        first_departure + timedelta(minutes=25),
                        first_departure + timedelta(minutes=25),
                        33.495,
                        126.505,
                    ),
                    (
                        "trip-second",
                        "370",
                        "stop-transfer",
                        1,
                        first_departure + timedelta(minutes=40),
                        first_departure + timedelta(minutes=40),
                        33.495,
                        126.505,
                    ),
                    (
                        "trip-second",
                        "370",
                        "stop-visit",
                        2,
                        final_arrival,
                        final_arrival,
                        33.5,
                        126.51,
                    ),
                )
            )

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_generation_gateway.psycopg.connect",
        lambda dsn: Connection(),
    )
    payload = make_request().model_dump(mode="python")
    payload["accommodation"] = {
        "place_id": "hotel",
        "name": "제주 숙소",
        "coordinates": {"latitude": 33.49, "longitude": 126.5},
    }
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
        "bus_wait_limit_minutes": 30,
    }
    request = RecommendDayTripsInput.model_validate(payload)
    place = cast(
        VerifiedGenerationPlace,
        SimpleNamespace(
            place_id="visit",
            position=Coordinates(latitude=33.5, longitude=126.51),
        ),
    )
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._runtime_dsn = "postgresql://runtime"
    gateway._planning_policy = load_planning_policy(
        ROOT / "config/policies/planning_policy_v1.toml"
    )

    legs = gateway._bus_candidate_legs(request, (place,))

    assert len(queries) == 4
    assert len(legs) == 1
    assert legs[0].origin_id == "hotel"
    assert legs[0].destination_id == "visit"
    assert legs[0].transfers == 1


def test_bus_candidate_skeleton_chooses_earliest_arrival_not_first_departure() -> None:
    """후보 뼈대는 먼저 출발한 느린 편이 아니라 문-to-문 도착이 빠른 운행편을 선택해야 한다."""

    policy = load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml")
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._planning_policy = policy
    current_at = datetime(2026, 8, 15, 9, tzinfo=UTC)
    slow = _BusCandidateLeg(
        origin_id="hotel",
        destination_id="visit",
        scheduled_departure_at=current_at + timedelta(minutes=10),
        scheduled_arrival_at=current_at + timedelta(minutes=90),
        access_distance_meters=0,
        egress_distance_meters=0,
        transfers=0,
    )
    fast = _BusCandidateLeg(
        origin_id="hotel",
        destination_id="visit",
        scheduled_departure_at=current_at + timedelta(minutes=15),
        scheduled_arrival_at=current_at + timedelta(minutes=45),
        access_distance_meters=0,
        egress_distance_meters=0,
        transfers=1,
    )
    request = make_request()
    arrival = gateway._next_bus_arrival((slow, fast), current_at, request)

    assert arrival == fast.scheduled_arrival_at + timedelta(
        minutes=policy.boarding_buffer_minutes.route_uncertainty
    )


def test_bus_only_fallback_prefers_verified_hours_and_distinct_edges() -> None:
    """버스 fallback은 검증된 운영시간 안에서 primary와 공유 구간이 적은 경로를 골라야 한다."""

    hotel = Coordinates(latitude=33.5, longitude=126.5)
    return_at = datetime(2026, 8, 15, 18, tzinfo=KST)

    def place(
        place_id: str,
        activity_type: Literal["visit", "meal", "rest"],
        *,
        latitude_offset: float,
        verified: bool,
    ):
        return SimpleNamespace(
            place_id=place_id,
            activity_type=activity_type,
            operating_hours_status="VERIFIED" if verified else "UNVERIFIED",
            position=Coordinates(
                latitude=33.5 + latitude_offset,
                longitude=126.5 + latitude_offset,
            ),
        )

    primary = ("far-a", "far-b", "far-meal", "far-c", "far-rest")
    shared_fallback = ("far-a", "far-b", "far-meal", "far-c", "alternate-rest")
    far_fallback = ("other-a", "other-b", "other-meal", "other-c", "other-rest")
    local_fallback = ("local-a", "local-meal", "local-b", "local-c", "local-rest")
    relaxed = {
        primary: ("far-a", "far-b", "far-meal", "far-rest"),
        shared_fallback: ("far-a", "far-b", "far-meal", "alternate-rest"),
        far_fallback: ("other-a", "other-b", "other-meal", "other-rest"),
        local_fallback: ("local-a", "local-b", "local-meal", "local-rest"),
    }
    def role(place_id: str) -> Literal["visit", "meal", "rest"]:
        if "meal" in place_id:
            return "meal"
        if "rest" in place_id:
            return "rest"
        return "visit"

    places = tuple(
        place(
            place_id,
            role(place_id),
            latitude_offset=latitude_offset,
            verified=verified,
        )
        for order, latitude_offset, verified in (
            (primary, 0.4, True),
            (shared_fallback, 0.0001, True),
            (far_fallback, 0.0005, True),
            (local_fallback, 0.001, False),
        )
        for place_id in order
    )
    solutions = {
        Strategy.BALANCED: (
            (return_at, primary),
            (return_at + timedelta(minutes=1), shared_fallback),
            (return_at + timedelta(minutes=5), far_fallback),
            (return_at + timedelta(minutes=10), local_fallback),
        ),
        Strategy.RELAXED: tuple(
            (time_value, relaxed[order])
            for time_value, order in (
                (return_at, primary),
                (return_at + timedelta(minutes=1), shared_fallback),
                (return_at + timedelta(minutes=5), far_fallback),
                (return_at + timedelta(minutes=10), local_fallback),
            )
        ),
        Strategy.EXPERIENCE_MAX: (
            (return_at, primary),
            (return_at + timedelta(minutes=1), shared_fallback),
            (return_at + timedelta(minutes=5), far_fallback),
            (return_at + timedelta(minutes=10), local_fallback),
        ),
    }
    payload = make_request().model_dump(mode="python")
    payload["accommodation"] = {
        "place_id": "hotel",
        "name": "제주 숙소",
        "coordinates": hotel.model_dump(),
    }
    request = RecommendDayTripsInput.model_validate(payload)
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._bus_candidate_legs = lambda request, places: ()
    gateway._bus_only_strategy_solutions = (
        lambda request, strategy, place_by_id, legs: solutions[strategy]
    )

    candidates = gateway.bus_only_candidate_orders(
        request,
        cast(tuple[VerifiedGenerationPlace, ...], places),
    )

    assert candidates[Strategy.BALANCED][0] == primary
    assert candidates[Strategy.BALANCED][1] == far_fallback


def test_bus_only_primary_places_verified_meal_early_before_route_reuse() -> None:
    """버스 primary는 경로 재사용보다 검증된 식사를 이른 순서에 배치해야 한다."""

    return_at = datetime(2026, 8, 15, 17, tzinfo=KST)
    late = ("visit-a", "visit-b", "meal", "visit-c", "rest")
    early = ("visit-a", "meal", "visit-b", "visit-c", "rest")
    relaxed_late = ("visit-a", "visit-b", "meal", "rest")
    relaxed_early = ("visit-a", "meal", "visit-b", "rest")

    def role(place_id: str) -> Literal["visit", "meal", "rest"]:
        if place_id == "meal":
            return "meal"
        if place_id == "rest":
            return "rest"
        return "visit"

    places = tuple(
        cast(
            VerifiedGenerationPlace,
            SimpleNamespace(
                place_id=place_id,
                activity_type=role(place_id),
                operating_hours_status="VERIFIED",
                position=Coordinates(latitude=33.5, longitude=126.5),
            ),
        )
        for place_id in late
    )
    solutions = {
        Strategy.BALANCED: (
            (return_at, late),
            (return_at + timedelta(minutes=5), early),
        ),
        Strategy.RELAXED: (
            (return_at, relaxed_late),
            (return_at + timedelta(minutes=5), relaxed_early),
        ),
        Strategy.EXPERIENCE_MAX: (
            (return_at, late),
            (return_at + timedelta(minutes=5), early),
        ),
    }
    payload = make_request().model_dump(mode="python")
    payload["accommodation"] = {
        "place_id": "hotel",
        "name": "제주 숙소",
        "coordinates": {"latitude": 33.5, "longitude": 126.5},
    }
    request = RecommendDayTripsInput.model_validate(payload)
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._bus_candidate_legs = lambda request, places: ()
    gateway._bus_only_strategy_solutions = (
        lambda request, strategy, place_by_id, legs: solutions[strategy]
    )

    candidates = gateway.bus_only_candidate_orders(request, places)

    assert candidates[Strategy.BALANCED][0] == early
    assert candidates[Strategy.RELAXED][0] == relaxed_early
    assert candidates[Strategy.EXPERIENCE_MAX][0] == early


def test_one_transfer_candidate_rejects_same_route_long_wait_and_far_stop() -> None:
    """1회 환승 후보는 동일 노선·30분 초과 대기·300m 초과 환승을 모두 제외해야 한다."""

    policy = load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml")
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._planning_policy = policy
    start = datetime(2026, 8, 15, 9, tzinfo=KST)
    common_first = (
        ("trip-first", "1111", "stop-hotel", 1, start, start, 33.49, 126.5),
        (
            "trip-first",
            "1111",
            "stop-transfer-a",
            2,
            start + timedelta(minutes=20),
            start + timedelta(minutes=20),
            33.5,
            126.5,
        ),
    )
    rows = (
        *common_first,
        (
            "trip-same-route",
            "1111",
            "stop-transfer-a",
            1,
            start + timedelta(minutes=35),
            start + timedelta(minutes=35),
            33.5,
            126.5,
        ),
        (
            "trip-same-route",
            "1111",
            "stop-visit",
            2,
            start + timedelta(minutes=50),
            start + timedelta(minutes=50),
            33.51,
            126.51,
        ),
        (
            "trip-long-wait",
            "370",
            "stop-transfer-a",
            1,
            start + timedelta(minutes=61),
            start + timedelta(minutes=61),
            33.5,
            126.5,
        ),
        (
            "trip-long-wait",
            "370",
            "stop-visit",
            2,
            start + timedelta(minutes=80),
            start + timedelta(minutes=80),
            33.51,
            126.51,
        ),
        (
            "trip-far-stop",
            "360",
            "stop-transfer-far",
            1,
            start + timedelta(minutes=35),
            start + timedelta(minutes=35),
            33.504,
            126.5,
        ),
        (
            "trip-far-stop",
            "360",
            "stop-visit",
            2,
            start + timedelta(minutes=50),
            start + timedelta(minutes=50),
            33.51,
            126.51,
        ),
    )

    candidates = gateway._one_transfer_candidate_rows(
        rows,
        {"stop-hotel", "stop-visit"},
        make_request(),
    )

    assert candidates == ()


def test_bus_only_strategy_can_be_built_entirely_from_transfer_candidate_legs() -> None:
    """버스 전용 전략 뼈대는 모든 장소 간 연결이 1회 환승이어도 완주안을 찾아야 한다."""

    policy = load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml")
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._planning_policy = policy
    start = datetime(2026, 8, 15, 9, tzinfo=KST)

    def place(
        place_id: str,
        activity_type: Literal["visit", "meal", "rest"],
        stay_minutes: int,
    ):
        category = "39" if activity_type in {"meal", "rest"} else "12"
        return VerifiedGenerationPlace(
            place_id=place_id,
            name=place_id,
            position=Coordinates(latitude=33.49, longitude=126.5),
            entrance_id=f"entrance-{place_id}",
            category=category,
            opens_at=start,
            closes_at=start + timedelta(hours=10),
            last_admission_at=None,
            stay_minutes=stay_minutes,
            is_estimated_stay=False,
            evidence_fact_ids=(f"fact-{place_id}",),
            activity_type=activity_type,
        )

    places = {
        item.place_id: item
        for item in (
            place("visit-a", "visit", 60),
            place("visit-b", "visit", 60),
            place("meal", "meal", 60),
            place("visit-c", "visit", 60),
            place("rest", "rest", 30),
        )
    }

    def leg(origin: str, destination: str, depart: datetime, arrive: datetime):
        return _BusCandidateLeg(
            origin_id=origin,
            destination_id=destination,
            scheduled_departure_at=depart,
            scheduled_arrival_at=arrive,
            access_distance_meters=0,
            egress_distance_meters=0,
            transfers=1,
        )

    legs = (
        leg("hotel", "visit-a", start + timedelta(minutes=10), start + timedelta(minutes=20)),
        leg(
            "visit-a",
            "visit-b",
            start + timedelta(minutes=105),
            start + timedelta(minutes=115),
        ),
        leg(
            "visit-b",
            "meal",
            start + timedelta(minutes=200),
            start + timedelta(minutes=210),
        ),
        leg(
            "meal",
            "visit-c",
            start + timedelta(minutes=295),
            start + timedelta(minutes=305),
        ),
        leg(
            "visit-c",
            "rest",
            start + timedelta(minutes=390),
            start + timedelta(minutes=400),
        ),
        leg(
            "rest",
            "hotel",
            start + timedelta(minutes=455),
            start + timedelta(minutes=465),
        ),
    )
    payload = make_request().model_dump(mode="python")
    payload["accommodation"] = {
        "place_id": "hotel",
        "name": "제주 숙소",
        "coordinates": {"latitude": 33.49, "longitude": 126.5},
    }
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
        "bus_wait_limit_minutes": 30,
    }
    request = RecommendDayTripsInput.model_validate(payload)

    solutions = gateway._bus_only_strategy_solutions(
        request,
        Strategy.BALANCED,
        places,
        legs,
    )

    assert solutions
    assert solutions[0][1] == ("visit-a", "visit-b", "meal", "visit-c", "rest")


def test_bus_only_strategy_keeps_soft_continuous_activity_limit_as_a_caution() -> None:
    """필수 휴식 편의가 없으면 버스 뼈대가 연속 활동 선호 초과만으로 사라지지 않아야 한다."""

    policy = load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml")
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._planning_policy = policy
    start = datetime(2026, 8, 15, 9, tzinfo=KST)

    def place(place_id: str) -> VerifiedGenerationPlace:
        return VerifiedGenerationPlace(
            place_id=place_id,
            name=place_id,
            position=Coordinates(latitude=33.49, longitude=126.5),
            entrance_id=f"entrance-{place_id}",
            category="12",
            opens_at=start,
            closes_at=start + timedelta(hours=10),
            last_admission_at=None,
            stay_minutes=60,
            is_estimated_stay=False,
            evidence_fact_ids=(f"fact-{place_id}",),
            activity_type="visit",
        )

    def leg(
        origin: str,
        destination: str,
        depart_minutes: int,
        arrive_minutes: int,
    ) -> _BusCandidateLeg:
        return _BusCandidateLeg(
            origin_id=origin,
            destination_id=destination,
            scheduled_departure_at=start + timedelta(minutes=depart_minutes),
            scheduled_arrival_at=start + timedelta(minutes=arrive_minutes),
            access_distance_meters=0,
            egress_distance_meters=0,
            transfers=0,
        )

    places = {
        item.place_id: item for item in (place("visit-a"), place("visit-b"), place("visit-c"))
    }
    legs = (
        leg("hotel", "visit-a", 10, 20),
        leg("visit-a", "visit-b", 105, 115),
        leg("visit-b", "visit-c", 200, 210),
        leg("visit-c", "hotel", 295, 305),
    )
    payload = make_request().model_dump(mode="python")
    payload["accommodation"] = {
        "place_id": "hotel",
        "name": "제주 숙소",
        "coordinates": {"latitude": 33.49, "longitude": 126.5},
    }
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
        "bus_wait_limit_minutes": 30,
    }
    payload["food"] = {"auto_schedule_meals": False, "auto_schedule_cafe": False}
    request = RecommendDayTripsInput.model_validate(payload)

    soft_solutions = gateway._bus_only_strategy_solutions(
        request,
        Strategy.BALANCED,
        places,
        legs,
    )
    hard_request = request.model_copy(
        update={
            "rest": request.rest.model_copy(update={"seat_requirement": "required"})
        }
    )
    hard_solutions = gateway._bus_only_strategy_solutions(
        hard_request,
        Strategy.BALANCED,
        places,
        legs,
    )

    assert soft_solutions
    assert hard_solutions == ()


def test_candidate_place_details_are_primed_with_four_batch_queries(monkeypatch) -> None:
    """장소 관측 상태는 장소 batch에 결합해 후보 수와 무관하게 전체 네 번에 읽어야 한다."""

    executed: list[str] = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            query = str(statement)
            executed.append(query)
            assert "ANY(%s::text[])" in query
            if "active_place_opening_rule" in query:
                return Result([])
            if "active_place_schedule_exception" in query:
                assert "active_place_weekly_closure" in query
                return Result([])
            if "active_place_entrance" in query:
                return Result(
                    [
                        (
                            "place-1",
                            "entrance-fact-1",
                            "main-1",
                            33.49,
                            126.5,
                            ["bus"],
                            "entrance-publication",
                            datetime.now(UTC),
                        )
                    ]
                )
            return Result(
                [
                    (
                        "place-1",
                        "tourapi.place",
                        "place-publication",
                        "장소 1",
                        "12",
                        "제주 주소",
                        33.49,
                        126.5,
                        make_request().trip_date,
                        datetime.now(UTC),
                        None,
                        "observation-fact-1",
                        "UNVERIFIABLE",
                        "OPENING_HOURS_UNVERIFIED",
                        "observation-publication",
                        "tourapi.place-intro",
                        None,
                        datetime.now(UTC),
                    )
                ]
            )

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_generation_gateway.psycopg.connect",
        lambda dsn: Connection(),
    )
    gateway = object.__new__(PostgresGenerationGateway)
    gateway._runtime_dsn = "postgresql://runtime"

    gateway._prime_place_candidates(("place-1", "place-2"), make_request())

    assert len(executed) == 4
    assert "active_place_opening_observation" in executed[0]
    assert gateway._place_rows["place-1"] is not None
    assert gateway._place_rows["place-2"] is None
    assert gateway._opening_rows == {"place-1": (), "place-2": ()}
    assert gateway._exception_rows == {"place-1": None, "place-2": None}
    assert len(gateway._entrance_rows["place-1"]) == 1
    assert gateway._entrance_rows["place-2"] == ()


def test_unverified_place_keeps_the_completed_observation_as_evidence(monkeypatch) -> None:
    """모호한 장소 추천은 영업 시각 대신 완료된 상세소개 관측 상태 fact를 근거로 남겨야 한다."""

    gateway = object.__new__(PostgresGenerationGateway)
    observed_at = datetime.now(UTC)
    gateway._place_rows = {
        "place-1": (
            "place-1",
            "tourapi.place",
            "place-publication",
            "장소 1",
            "12",
            "제주 주소",
            33.49,
            126.5,
            make_request().trip_date,
            observed_at,
            None,
            "observation-fact-1",
            "UNVERIFIABLE",
            "OPENING_HOURS_UNVERIFIED",
            "observation-publication",
            "tourapi.place-intro",
            None,
            observed_at,
        )
    }
    gateway._opening_rows = {"place-1": ()}
    gateway._exception_rows = {"place-1": None}
    gateway._entrances = {}
    gateway._planning_policy = load_planning_policy(
        ROOT / "config/policies/planning_policy_v1.toml"
    )
    captured: list[tuple[object, ...]] = []
    endpoint = gateway._representative_endpoint(
        place_id="place-1",
        position=Coordinates(latitude=33.49, longitude=126.5),
        allowed_modes={"walk"},
    )
    monkeypatch.setattr(gateway, "_load_route_endpoints", lambda *args, **kwargs: (endpoint,))
    monkeypatch.setattr(gateway, "_add_source_fact", lambda *args: captured.append(args))
    monkeypatch.setattr(gateway, "_policy_fact_id", lambda key: "policy:stay")

    place = gateway._load_place("place-1", make_request())

    assert place is not None
    assert place.operating_hours_status == "UNVERIFIED"
    assert "observation-fact-1" in place.evidence_fact_ids
    observation = next(item for item in captured if item[0] == "observation-fact-1")
    assert observation[2] == {
        "observation_status": "UNVERIFIABLE",
        "reason_code": "OPENING_HOURS_UNVERIFIED",
    }
