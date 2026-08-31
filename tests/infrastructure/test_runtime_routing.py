"""운영용 TMAP 문-to-문 route planner 테스트."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Lock
from time import sleep
from types import SimpleNamespace

import httpx
import pytest

from jeju_trip.domain.models import (
    Coordinates,
    DataSourceMetadata,
    Derivation,
    EndpointBasis,
    EvidenceFact,
    RecommendDayTripsInput,
    Strategy,
    WalkConnection,
)
from jeju_trip.infrastructure.runtime_routing import (
    TmapDoorRoutePlanner,
    VerifiedEntrance,
    _BusStopCandidate,
)
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.infrastructure.tmap_adapter import HttpxJsonTransport
from jeju_trip.planning.execution_budget import ExecutionBudget
from jeju_trip.planning.policy import (
    load_bus_fare_policy,
    load_planning_policy,
    load_taxi_fare_policy,
)
from tests.factories import make_request

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))


def _bus_stop_candidate(identifier: str, index: int) -> _BusStopCandidate:
    return _BusStopCandidate(
        canonical_stop_id=f"canonical-{identifier}",
        provider_stop_id=f"provider-{identifier}",
        name=identifier,
        direction_text="동쪽",
        position=Coordinates(latitude=33.5, longitude=126.5 + index / 10_000),
        stop_fact_id=identifier,
        identity_fact_id=f"identity-{identifier}",
        stop_publication_id="pub-stop",
        identity_publication_id="pub-identity",
    )


def _walk_only_request() -> RecommendDayTripsInput:
    payload = make_request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("walk",),
        "preferred_mode": "walk",
        "selection_policy": "prefer_selected",
        "fallback_order": (),
        "max_transfers_per_leg": 0,
    }
    return RecommendDayTripsInput.model_validate(payload)


def _taxi_only_request() -> RecommendDayTripsInput:
    payload = make_request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("taxi",),
        "preferred_mode": "taxi",
        "selection_policy": "prefer_selected",
        "fallback_order": (),
        "max_transfers_per_leg": 0,
    }
    return RecommendDayTripsInput.model_validate(payload)


def test_walk_route_uses_tmap_summary_and_policy_multiplier() -> None:
    """직접 도보는 TMAP 시간에 정책 배수와 불확실성만 더해 계산해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {"totalDistance": 600, "totalTime": 600},
                        "geometry": {"type": "LineString", "coordinates": []},
                    }
                ]
            },
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    origin = VerifiedEntrance(
        "entrance-a",
        "place-a",
        Coordinates(latitude=33.5, longitude=126.5),
        ("walk",),
    )
    destination = VerifiedEntrance(
        "entrance-b",
        "place-b",
        Coordinates(latitude=33.501, longitude=126.501),
        ("walk",),
    )
    departure_at = datetime(2026, 8, 15, 9, tzinfo=KST)
    result = planner.plan(
        (origin,),
        (destination,),
        departure_at,
        Strategy.BALANCED,
        _walk_only_request(),
        ExecutionBudget.generation(),
    )
    assert result is not None
    assert result.option.duration_minutes == 15
    assert result.option.transfer.direct_walk is not None
    assert result.option.transfer.direct_walk.expected_minutes == 10
    assert result.option.transfer.distance_meters == 600
    route_fact = next(fact for fact in result.evidence_facts if fact.category == "walking_route")
    assert route_fact.data_as_of == route_fact.retrieved_at
    assert route_fact.data_as_of != departure_at
    assert "geometry" not in repr(result)


def test_bus_access_walk_limit_uses_policy_adjusted_minutes() -> None:
    """버스 접근 20분 상한은 정책 배수와 불확실성을 합친 시간에 적용해야 한다."""

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "features": [
                                {
                                    "properties": {
                                        "totalDistance": 1_170,
                                        "totalTime": 1_020,
                                    }
                                }
                            ]
                        },
                    )
                )
            )
        ),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    result = planner._walking_leg(
        VerifiedEntrance(
            "place-gate",
            "place",
            Coordinates(latitude=33.5, longitude=126.5),
            ("bus",),
        ),
        VerifiedEntrance(
            "stop-gate",
            "stop",
            Coordinates(latitude=33.501, longitude=126.501),
            ("walk",),
            EndpointBasis.CONFIRMED_STOP,
        ),
        datetime(2026, 8, 15, 9, tzinfo=KST),
        Strategy.BALANCED,
        make_request(),
        ExecutionBudget.generation(),
        "access_walk",
    )

    assert result is None


def test_same_confirmed_transfer_stop_does_not_call_tmap() -> None:
    """동일 confirmed 정류장 환승은 0m 연속성으로 처리하고 TMAP을 호출하지 않아야 한다."""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"features": [{"properties": {"totalDistance": 1, "totalTime": 1}}]},
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    endpoint = VerifiedEntrance(
        "jeju.stop:confirmed:1",
        "jeju.stop:confirmed:1",
        Coordinates(latitude=33.5, longitude=126.5),
        ("walk",),
        EndpointBasis.CONFIRMED_STOP,
        ("stop-fact-1", "identity-fact-1"),
    )
    budget = ExecutionBudget.generation()

    result = planner._walking_leg(
        endpoint,
        endpoint,
        datetime(2026, 8, 15, 9, tzinfo=KST),
        Strategy.BALANCED,
        make_request(),
        budget,
        "transfer_walk",
    )

    assert result is not None
    walk, fact, source = result
    assert walk.distance_meters == 0
    assert walk.planned_minutes == 0
    assert fact.derivation.input_fact_ids == ("stop-fact-1", "identity-fact-1")
    assert source.source_id == "transport.stop-identity-map"
    assert calls == 0
    assert budget.external_calls == 0


def test_route_timeout_becomes_unavailable_candidate() -> None:
    """TMAP 시간초과는 공개 생성을 중단하지 않고 해당 수단의 근거 없음으로 처리해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("비공개 외부 오류", request=request)

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    origin = VerifiedEntrance(
        "entrance-a", "place-a", Coordinates(latitude=33.5, longitude=126.5), ("walk",)
    )
    destination = VerifiedEntrance(
        "entrance-b", "place-b", Coordinates(latitude=33.501, longitude=126.501), ("walk",)
    )

    result = planner.plan(
        (origin,),
        (destination,),
        datetime(2026, 8, 14, 9, tzinfo=KST),
        Strategy.BALANCED,
        _walk_only_request(),
        ExecutionBudget.generation(),
    )

    assert result is None


def test_cost_time_balance_selects_short_walk_and_preserves_taxi_comparison() -> None:
    """실제 door-route planner도 15분 도보를 우선하고 택시 호출·주행 비교를 보존해야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "pedestrian" in str(request.url):
            return httpx.Response(
                200,
                json={"features": [{"properties": {"totalDistance": 600, "totalTime": 480}}]},
            )
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "totalDistance": 2_000,
                            "totalTime": 60,
                            "totalFare": 0,
                        }
                    }
                ]
            },
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    payload = make_request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("walk", "taxi"),
        "preferred_mode": "taxi",
        "selection_policy": "cost_time_balance",
        "fallback_order": ("walk",),
    }
    request = RecommendDayTripsInput.model_validate(payload)
    origin = VerifiedEntrance(
        "gate-a",
        "place-a",
        Coordinates(latitude=33.5, longitude=126.5),
        ("walk", "taxi"),
        EndpointBasis.REPRESENTATIVE_PLACE_POINT,
    )
    destination = VerifiedEntrance(
        "gate-b",
        "place-b",
        Coordinates(latitude=33.501, longitude=126.501),
        ("walk", "taxi"),
    )

    result = planner.plan(
        (origin,),
        (destination,),
        datetime(2026, 8, 14, 9, tzinfo=KST),
        Strategy.RELAXED,
        request,
        ExecutionBudget.generation(),
    )

    assert result is not None
    assert result.option.transfer.mode == "walk"
    assert result.option.transfer.mode_decision is not None
    assert result.option.transfer.mode_decision.reason_codes == ("WALK_WITHIN_15_MINUTES",)
    assert (
        result.option.transfer.mode_decision.origin_basis
        == EndpointBasis.REPRESENTATIVE_PLACE_POINT
    )
    assert result.option.transfer.direct_walk is not None
    assert result.option.transfer.direct_walk.entrance_verification == "PROVISIONAL_PLACE_POINT"
    taxi = next(item for item in result.option.transfer.alternatives if item.mode == "taxi")
    assert taxi.pickup_buffer_minutes == 10
    assert taxi.driving_minutes == 1
    assert taxi.duration_minutes == 11


def test_cost_balance_computes_independent_modes_concurrently() -> None:
    """같은 출발시각의 도보·택시 후보는 실행예산 동시성 안에서 함께 조회해야 한다."""

    barrier = Barrier(2)

    def handler(request: httpx.Request) -> httpx.Response:
        barrier.wait(timeout=1)
        if "pedestrian" in str(request.url):
            return httpx.Response(
                200,
                json={"features": [{"properties": {"totalDistance": 500, "totalTime": 420}}]},
            )
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "totalDistance": 2_000,
                            "totalTime": 600,
                            "totalFare": 0,
                        }
                    }
                ]
            },
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    payload = make_request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("walk", "taxi"),
        "preferred_mode": "taxi",
        "selection_policy": "cost_time_balance",
        "fallback_order": ("walk",),
    }
    request = RecommendDayTripsInput.model_validate(payload)
    origin = VerifiedEntrance(
        "gate-a", "place-a", Coordinates(latitude=33.5, longitude=126.5), ("walk", "taxi")
    )
    destination = VerifiedEntrance(
        "gate-b", "place-b", Coordinates(latitude=33.501, longitude=126.501), ("walk", "taxi")
    )

    result = planner.plan(
        (origin,),
        (destination,),
        datetime(2026, 8, 14, 9, tzinfo=KST),
        Strategy.RELAXED,
        request,
        ExecutionBudget.generation(),
    )

    assert result is not None
    assert result.option.transfer.mode == "walk"


def test_cost_balance_skips_provably_slow_walk_but_preserves_alternative() -> None:
    """최대 계획속도에서도 15분을 넘는 도보는 호출 없이 근거 있는 미선택 대안으로 남겨야 한다."""

    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(str(request.url))
        assert "pedestrian" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "totalDistance": 3_000,
                            "totalTime": 600,
                            "totalFare": 0,
                        }
                    }
                ]
            },
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    payload = make_request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("walk", "taxi"),
        "preferred_mode": "taxi",
        "selection_policy": "cost_time_balance",
        "fallback_order": ("walk",),
    }
    request = RecommendDayTripsInput.model_validate(payload)
    origin = VerifiedEntrance(
        "gate-a", "place-a", Coordinates(latitude=33.45, longitude=126.7), ("walk", "taxi")
    )
    destination = VerifiedEntrance(
        "gate-b", "place-b", Coordinates(latitude=33.47, longitude=126.7), ("walk", "taxi")
    )

    result = planner.plan(
        (origin,),
        (destination,),
        datetime(2026, 8, 14, 9, tzinfo=KST),
        Strategy.RELAXED,
        request,
        ExecutionBudget.generation(),
    )

    assert result is not None
    assert len(requested_paths) == 1
    walk = next(item for item in result.option.transfer.alternatives if item.mode == "walk")
    assert walk.status == "unavailable"
    assert walk.reason_codes == ("WALK_EXCEEDS_DIRECT_LIMIT_LOWER_BOUND",)


def test_tmap_memory_cache_hits_do_not_consume_external_call_budget() -> None:
    """동일 TMAP 경로의 메모리 cache hit는 생성 외부 호출 예산에서 제외해야 한다."""

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "features": [
                                {
                                    "properties": {
                                        "totalDistance": 600,
                                        "totalTime": 480,
                                    }
                                }
                            ]
                        },
                    )
                )
            )
        ),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    origin = VerifiedEntrance(
        "gate-a", "place-a", Coordinates(latitude=33.5, longitude=126.5), ("walk",)
    )
    destination = VerifiedEntrance(
        "gate-b", "place-b", Coordinates(latitude=33.501, longitude=126.501), ("walk",)
    )
    departure = datetime(2026, 8, 14, 9, tzinfo=KST)
    budget = ExecutionBudget.generation()

    for _ in range(2):
        result = planner.plan(
            (origin,),
            (destination,),
            departure,
            Strategy.RELAXED,
            _walk_only_request(),
            budget,
        )
        assert result is not None

    assert budget.calls_by_source == {"tmap.pedestrian": 1}


def test_tmap_route_cache_reuses_same_geometry_across_departure_times() -> None:
    """출발시각을 전송하지 않는 TMAP 경로는 같은 좌표에서 전략별로 다시 호출하지 않아야 한다."""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"features": [{"properties": {"totalDistance": 600, "totalTime": 480}}]},
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    origin = VerifiedEntrance(
        "gate-a", "place-a", Coordinates(latitude=33.5, longitude=126.5), ("walk",)
    )
    destination = VerifiedEntrance(
        "gate-b", "place-b", Coordinates(latitude=33.501, longitude=126.501), ("walk",)
    )
    budget = ExecutionBudget.generation()

    for hour in (9, 11):
        assert (
            planner.plan(
                (origin,),
                (destination,),
                datetime(2026, 8, 14, hour, tzinfo=KST),
                Strategy.RELAXED,
                _walk_only_request(),
                budget,
            )
            is not None
        )

    assert calls == 1
    assert budget.calls_by_source == {"tmap.pedestrian": 1}


def test_tmap_raw_route_cache_is_independent_of_allowed_mode_set() -> None:
    """같은 TMAP 원시 경로는 생성과 고정수단 평가의 허용수단 집합이 달라도 재사용해야 한다."""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "totalDistance": 2_000,
                            "totalTime": 600,
                            "totalFare": 0,
                        }
                    }
                ]
            },
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    origin = VerifiedEntrance(
        "gate-a", "place-a", Coordinates(latitude=33.5, longitude=126.5), ("taxi", "walk")
    )
    destination = VerifiedEntrance(
        "gate-b", "place-b", Coordinates(latitude=33.51, longitude=126.51), ("taxi", "walk")
    )
    mixed_payload = _taxi_only_request().model_dump(mode="python")
    mixed_payload["transport"]["allowed_modes"] = ("taxi", "walk")
    mixed_payload["transport"]["fallback_order"] = ("walk",)
    mixed_request = RecommendDayTripsInput.model_validate(mixed_payload)
    budget = ExecutionBudget.generation()

    for request in (mixed_request, _taxi_only_request()):
        result = planner.plan(
            (origin,),
            (destination,),
            datetime(2026, 8, 14, 9, tzinfo=KST),
            Strategy.RELAXED,
            request,
            budget,
        )
        assert result is not None
        assert result.option.transfer.mode == "taxi"

    assert calls == 1
    assert budget.calls_by_source == {"tmap.driving": 1}


def test_tmap_route_cache_prevents_concurrent_duplicate_calls() -> None:
    """세 전략이 같은 좌표를 동시에 요청해도 TMAP 실제 호출은 한 번만 수행해야 한다."""

    calls = 0
    calls_lock = Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.05)
        return httpx.Response(
            200,
            json={"features": [{"properties": {"totalDistance": 600, "totalTime": 480}}]},
        )

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler))),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    origin = VerifiedEntrance(
        "gate-a", "place-a", Coordinates(latitude=33.5, longitude=126.5), ("walk",)
    )
    destination = VerifiedEntrance(
        "gate-b", "place-b", Coordinates(latitude=33.501, longitude=126.501), ("walk",)
    )
    budget = ExecutionBudget.generation()

    def invoke(hour: int):
        return planner.plan(
            (origin,),
            (destination,),
            datetime(2026, 8, 14, hour, tzinfo=KST),
            Strategy.RELAXED,
            _walk_only_request(),
            budget,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = tuple(executor.map(invoke, (9, 10, 11)))

    assert all(result is not None for result in results)
    assert calls == 1
    assert budget.calls_by_source == {"tmap.pedestrian": 1}


def test_cost_balance_prefilters_same_direction_exact_stop_pair(monkeypatch) -> None:
    """비용·시간 비교 버스는 같은 방향·순서·exact stop-time 정류장 한 쌍부터 골라야 한다."""

    row = (
        "canonical-board",
        "provider-board",
        "고성환승정류장[남]",
        "성산 방면",
        33.45,
        126.91,
        "stop-board",
        "identity-board",
        "publication-stop-board",
        "publication-identity-board",
        "canonical-alight",
        "provider-alight",
        "성산일출봉입구[동]",
        "성산 방면",
        33.46,
        126.93,
        "stop-alight",
        "identity-alight",
        "publication-stop-alight",
        "publication-identity-alight",
    )

    class Result:
        def fetchone(self):
            return row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            query = str(statement)
            assert "board_sequence.route_sequence < alight_sequence.route_sequence" in query
            assert "active_stop_time" in query
            assert query.count("LIMIT 12") == 2
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_routing.psycopg.connect",
        lambda dsn: Connection(),
    )
    planner = object.__new__(TmapDoorRoutePlanner)
    planner._runtime_dsn = "postgresql://runtime"

    pair = planner._nearest_direct_stop_pair(
        Coordinates(latitude=33.45, longitude=126.91),
        Coordinates(latitude=33.46, longitude=126.93),
    )

    assert pair is not None
    assert pair[0].provider_stop_id == "provider-board"
    assert pair[1].provider_stop_id == "provider-alight"


def test_exact_bus_beyond_wait_limit_is_unavailable_not_unverifiable(monkeypatch) -> None:
    """stop-time이 있지만 대기 상한을 넘는 버스는 제약 탈락이어야 한다."""

    planner = object.__new__(TmapDoorRoutePlanner)
    planner._planning_policy = load_planning_policy(
        ROOT / "config/policies/planning_policy_v1.toml"
    )
    board = SimpleNamespace(stop_fact_id="stop-board")
    monkeypatch.setattr(planner, "_nearest_stops", lambda position: (board,))
    departure = datetime(2026, 8, 15, 9, tzinfo=KST)
    schedule = (
        "trip-fact",
        "route-fact",
        "provider-route",
        "201",
        "일반간선버스",
        "board-time",
        "alight-time",
        departure + timedelta(minutes=60),
        departure + timedelta(minutes=90),
        "동쪽",
        "pub-timetable",
    )
    monkeypatch.setattr(planner, "_scheduled_direct_bus", lambda *args: schedule)
    endpoint = VerifiedEntrance(
        "gate",
        "place",
        Coordinates(latitude=33.5, longitude=126.5),
        ("bus",),
    )

    result = planner._exact_bus_unavailable(endpoint, endpoint, departure, make_request())

    assert result is not None
    candidate, _ = result
    assert candidate.status == "unavailable"
    assert candidate.reason_codes == ("BUS_WAIT_LIMIT_EXCEEDED",)


def test_direct_bus_stop_pairs_are_prefiltered_in_one_batch_query(monkeypatch) -> None:
    """직통 버스 정류장 후보는 12×12 개별 조회 대신 배열 SQL 한 번으로 압축해야 한다."""

    schedule_queries: list[tuple[str, dict]] = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            query = str(statement)
            if "active_holiday" in query:
                return Result([])
            schedule_queries.append((query, parameters))
            return Result([("board-2", "alight-1", datetime(2026, 8, 18, 2, tzinfo=UTC))])

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_routing.psycopg.connect",
        lambda dsn: Connection(),
    )
    planner = object.__new__(TmapDoorRoutePlanner)
    planner._runtime_dsn = "postgresql://runtime"
    boarding = tuple(
        _bus_stop_candidate(f"board-{index}", index) for index in range(1, 13)
    )
    alighting = tuple(
        _bus_stop_candidate(f"alight-{index}", index) for index in range(1, 13)
    )

    signatures = planner._direct_stop_signatures(
        boarding,
        alighting,
        datetime(2026, 8, 18, 9, tzinfo=KST),
        date(2026, 8, 18),
        limit=6,
    )

    assert signatures == (("board-2", "alight-1"),)
    assert len(schedule_queries) == 1
    query, parameters = schedule_queries[0]
    assert "ANY(%(boarding_stops)s::text[])" in query
    assert "ANY(%(alighting_stops)s::text[])" in query
    assert parameters["boarding_stops"] == [f"board-{index}" for index in range(1, 13)]
    assert parameters["alighting_stops"] == [f"alight-{index}" for index in range(1, 13)]
    assert parameters["limit"] == 6


def test_direct_bus_signatures_prefer_nearby_stop_pair_before_tmap() -> None:
    """직통 후보는 동일한 exact 후보군 안에서 양 끝 정류장 거리 순위를 먼저 반영해야 한다."""

    boarding = (
        _bus_stop_candidate("board-near", 1),
        _bus_stop_candidate("board-far", 2),
    )
    alighting = (
        _bus_stop_candidate("alight-near", 1),
        _bus_stop_candidate("alight-far", 2),
    )

    ranked = TmapDoorRoutePlanner._rank_direct_stop_signatures(
        (("board-far", "alight-far"), ("board-near", "alight-near")),
        boarding,
        alighting,
    )

    assert ranked[0] == ("board-near", "alight-near")


def test_transfer_bus_paths_are_prefiltered_in_one_batch_query(monkeypatch) -> None:
    """1회 환승 후보는 service day와 stop-time을 한 번의 SQL로 확인해 여섯 개로 줄여야 한다."""

    schedule_queries: list[tuple[str, dict]] = []
    row = (
        "board-1",
        "alight-2",
        "canonical-from",
        "provider-from",
        "환승 하차",
        "동쪽",
        33.5,
        126.55,
        "transfer-from",
        "identity-from",
        "pub-stop",
        "pub-identity",
        "canonical-to",
        "provider-to",
        "환승 승차",
        "동쪽",
        33.501,
        126.551,
        "transfer-to",
        "identity-to",
        "pub-stop",
        "pub-identity",
    )

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            query = str(statement)
            if "active_holiday" in query:
                return Result([])
            schedule_queries.append((query, parameters))
            return Result([row])

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_routing.psycopg.connect",
        lambda dsn: Connection(),
    )
    planner = object.__new__(TmapDoorRoutePlanner)
    planner._runtime_dsn = "postgresql://runtime"
    boarding = tuple(
        _bus_stop_candidate(f"board-{index}", index) for index in range(1, 13)
    )
    alighting = tuple(
        _bus_stop_candidate(f"alight-{index}", index) for index in range(1, 13)
    )

    paths = planner._transfer_stop_pairs_batch(
        boarding,
        alighting,
        datetime(2026, 8, 18, 9, tzinfo=KST),
        date(2026, 8, 18),
        limit=6,
    )

    assert tuple(paths) == (("board-1", "alight-2"),)
    assert paths[("board-1", "alight-2")][0][0].stop_fact_id == "transfer-from"
    assert paths[("board-1", "alight-2")][0][1].stop_fact_id == "transfer-to"
    assert len(schedule_queries) == 1
    query, parameters = schedule_queries[0]
    assert "WITH eligible_trip AS" in query
    assert "transfer_pair AS MATERIALIZED" in query
    assert "JOIN travel_read.active_bus_stop to_stop" in query
    assert "JOIN confirmed_stop to_identity" not in query
    assert "second_route.route_number <> first_route.route_number" in query
    assert "path_rank = 1" in query
    assert "second_arrival_at < %(arrive_before)s" in query
    assert parameters["boarding_stops"] == [f"board-{index}" for index in range(1, 13)]
    assert parameters["alighting_stops"] == [f"alight-{index}" for index in range(1, 13)]
    assert parameters["limit"] == 6
    assert parameters["arrive_before"] is None


def test_driving_route_does_not_claim_future_departure_as_observation_time() -> None:
    """일반 TMAP 차량 경로는 미래 출발시각을 교통 관측시각으로 기록하지 않아야 한다."""

    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        HttpxJsonTransport(
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "features": [
                                {
                                    "properties": {
                                        "totalDistance": 10000,
                                        "totalTime": 1200,
                                        "totalFare": 0,
                                    }
                                }
                            ]
                        },
                    )
                )
            )
        ),
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
    )
    origin = VerifiedEntrance(
        "vehicle-a",
        "place-a",
        Coordinates(latitude=33.5, longitude=126.5),
        ("taxi",),
    )
    destination = VerifiedEntrance(
        "vehicle-b",
        "place-b",
        Coordinates(latitude=33.6, longitude=126.7),
        ("taxi",),
    )
    departure_at = datetime(2026, 8, 15, 9, tzinfo=KST)

    result = planner.plan(
        (origin,),
        (destination,),
        departure_at,
        Strategy.BALANCED,
        _taxi_only_request(),
        ExecutionBudget.generation(),
    )

    assert result is not None
    route_fact = next(fact for fact in result.evidence_facts if fact.category == "driving_route")
    assert route_fact.data_as_of == route_fact.retrieved_at
    assert route_fact.data_as_of != departure_at
    assert route_fact.value["traffic_basis"] == "current_snapshot_proxy_for_future"
    assert route_fact.is_estimated is True
    driving_source = next(
        source for source in result.data_sources if source.source_id == "tmap.driving"
    )
    assert driving_source.data_as_of == route_fact.data_as_of


def test_bus_route_combines_confirmed_stops_timetable_and_endpoint_walks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """버스 경로는 confirmed 정류장·공식 stop time·양 끝 TMAP 도보를 모두 합쳐야 한다."""

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, parameters):
            statement = str(query)
            if "active_bus_fare_policy" in statement:
                return Cursor(
                    [
                        ("pub-bus-fare", "EXPRESS", 2000, 3000, 1000, 1500),
                        ("pub-bus-fare", "STANDARD", 1150, 1200, 350, 400),
                    ]
                )
            if "active_holiday" in statement:
                return Cursor([])
            if "active_bus_stop" in statement:
                longitude = parameters[0]
                if longitude < 126.6:
                    return Cursor(
                        [
                            (
                                "canonical-a",
                                "provider-a",
                                "출발 정류장",
                                "동쪽",
                                33.501,
                                126.501,
                                "stop-a",
                                "identity-a",
                                "pub-stop",
                                "pub-identity",
                            )
                        ]
                    )
                return Cursor(
                    [
                        (
                            "canonical-b",
                            "provider-b",
                            "도착 정류장",
                            "동쪽",
                            33.601,
                            126.701,
                            "stop-b",
                            "identity-b",
                            "pub-stop",
                            "pub-identity",
                        )
                    ]
                )
            if "active_scheduled_trip" in statement:
                assert "route.observed_at" not in statement
                return Cursor(
                    [
                        (
                            "trip-fact",
                            "route-fact",
                            "provider-route",
                            "201",
                            "일반간선버스",
                            "time-a",
                            "time-b",
                            datetime(2026, 8, 15, 0, 40, tzinfo=UTC),
                            datetime(2026, 8, 15, 1, 10, tzinfo=UTC),
                            "동쪽",
                            "pub-timetable",
                        )
                    ]
                )
            raise AssertionError("예상하지 않은 runtime query")

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_routing.psycopg.connect",
        lambda dsn: Connection(),
    )
    transport = HttpxJsonTransport(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "features": [
                            {
                                "properties": {
                                    "totalDistance": 800,
                                    "totalTime": 600,
                                }
                            }
                        ]
                    },
                )
            )
        )
    )
    planner = TmapDoorRoutePlanner(
        SourceCatalog.load(ROOT / "config/data_sources.toml"),
        transport,
        "secret",
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
        load_taxi_fare_policy(ROOT / "config/policies/jeju_taxi_fare_2024-07-01.toml"),
        runtime_dsn="runtime-dsn",
        bus_fare_policy=load_bus_fare_policy(
            ROOT / "config/policies/jeju_bus_fare_2026-08-10.toml"
        ),
    )
    monkeypatch.setattr(
        planner,
        "_one_transfer_bus",
        lambda *args, **kwargs: pytest.fail(
            "버스-only의 유효 직통 뒤에는 환승 후보를 다시 조회하면 안 된다."
        ),
    )
    payload = make_request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "selection_policy": "prefer_selected",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
    }
    request = RecommendDayTripsInput.model_validate(payload)
    result = planner.plan(
        (
            VerifiedEntrance(
                "origin-gate",
                "origin",
                Coordinates(latitude=33.5, longitude=126.5),
                ("bus",),
            ),
        ),
        (
            VerifiedEntrance(
                "destination-gate",
                "destination",
                Coordinates(latitude=33.6, longitude=126.7),
                ("bus",),
            ),
        ),
        datetime(2026, 8, 15, 9, tzinfo=KST),
        Strategy.BALANCED,
        request,
        ExecutionBudget.generation(),
    )
    assert result is not None
    assert result.option.transfer.mode == "bus"
    assert result.option.transfer.access_walk is not None
    assert result.option.transfer.egress_walk is not None
    assert result.option.transfer.access_walk.expected_minutes == 10
    assert result.option.transfer.access_walk.planned_minutes == 15
    assert result.option.transfer.bus_rides[0].route_number == "201"
    assert result.option.transfer.bus_rides[0].scheduled_departure_at.utcoffset() == timedelta(
        hours=9
    )
    assert result.option.transfer.bus_rides[0].scheduled_arrival_at.utcoffset() == timedelta(
        hours=9
    )
    assert result.option.cost_max_krw == 1200


def test_one_transfer_bus_preserves_two_rides_and_transfer_walk(monkeypatch) -> None:
    """1회 환승 버스는 두 공식 운행편과 접근·환승·하차 도보를 손실 없이 보존해야 한다."""

    def stop(identifier: str, longitude: float) -> _BusStopCandidate:
        return _BusStopCandidate(
            canonical_stop_id=f"canonical-{identifier}",
            provider_stop_id=f"provider-{identifier}",
            name=identifier,
            direction_text="동쪽",
            position=Coordinates(latitude=33.5, longitude=longitude),
            stop_fact_id=f"stop-{identifier}",
            identity_fact_id=f"identity-{identifier}",
            stop_publication_id="pub-stop",
            identity_publication_id="pub-identity",
        )

    boarding = stop("board", 126.50)
    transfer_from = stop("transfer-from", 126.55)
    transfer_to = stop("transfer-to", 126.551)
    alighting = stop("alight", 126.60)
    departure = datetime(2026, 8, 15, 9, tzinfo=KST)
    first_schedule = (
        "trip-first",
        "route-first",
        "provider-route-first",
        "201",
        "일반간선버스",
        "first-board-time",
        "first-alight-time",
        departure + timedelta(minutes=15),
        departure + timedelta(minutes=30),
        "동쪽",
        "pub-timetable",
    )
    second_schedule = (
        "trip-second",
        "route-second",
        "provider-route-second",
        "211",
        "일반간선버스",
        "second-board-time",
        "second-alight-time",
        departure + timedelta(minutes=50),
        departure + timedelta(minutes=70),
        "동쪽",
        "pub-timetable",
    )

    planner = object.__new__(TmapDoorRoutePlanner)
    planner._planning_policy = load_planning_policy(
        ROOT / "config/policies/planning_policy_v1.toml"
    )
    planner._bus_fare_policy = load_bus_fare_policy(
        ROOT / "config/policies/jeju_bus_fare_2026-08-10.toml"
    )
    monkeypatch.setattr(
        planner,
        "_transfer_stop_pairs",
        lambda board, alight: ((transfer_from, transfer_to),),
    )
    monkeypatch.setattr(
        planner,
        "_scheduled_direct_bus",
        lambda board, alight, ready, trip_date: (
            first_schedule if board.stop_fact_id == boarding.stop_fact_id else second_schedule
        ),
    )

    def walking(origin, destination, started_at, strategy, request, budget, kind):
        fact_id = f"fact-{kind}"
        walk = WalkConnection(
            kind=kind,
            from_id=origin.entrance_id,
            to_id=destination.entrance_id,
            distance_meters=200,
            expected_minutes=4,
            speed_multiplier=1,
            route_uncertainty_minutes=1,
            planned_minutes=5,
            entrance_verification="VERIFIED",
            evidence_fact_ids=(fact_id,),
        )
        fact = EvidenceFact(
            fact_id=fact_id,
            category="walking_route",
            value={"minutes": 4},
            data_as_of=date(2026, 8, 15),
            retrieved_at=departure,
            confidence=1,
            derivation=Derivation(kind="policy"),
        )
        source = DataSourceMetadata(
            source_id="tmap.pedestrian",
            provider="TMAP",
            dataset_version=None,
            data_as_of=departure,
            retrieved_at=departure,
            status="ACTIVE",
            attribution_text="TMAP",
        )
        return walk, fact, source

    monkeypatch.setattr(planner, "_walking_leg", walking)
    monkeypatch.setattr(planner, "_fare_policy_publication", lambda *args: "pub-fare")
    monkeypatch.setattr(
        planner,
        "_source_metadata",
        lambda source_id, observed_at: DataSourceMetadata(
            source_id=source_id,
            provider=source_id,
            dataset_version=None,
            data_as_of=observed_at,
            retrieved_at=observed_at,
            status="ACTIVE",
            attribution_text=source_id,
        ),
    )
    payload = make_request().model_dump(mode="python")
    payload["transport"] = {
        "allowed_modes": ("bus",),
        "preferred_mode": "bus",
        "selection_policy": "prefer_selected",
        "fallback_order": (),
        "max_transfers_per_leg": 1,
    }
    request = RecommendDayTripsInput.model_validate(payload)
    result = planner._one_transfer_bus(
        VerifiedEntrance(
            "origin-gate",
            "origin",
            Coordinates(latitude=33.5, longitude=126.49),
            ("bus",),
        ),
        VerifiedEntrance(
            "destination-gate",
            "destination",
            Coordinates(latitude=33.5, longitude=126.61),
            ("bus",),
        ),
        departure,
        Strategy.BALANCED,
        request,
        ExecutionBudget.generation(),
        (boarding,),
        (alighting,),
    )

    assert result is not None
    assert result.option.transfers == 1
    assert tuple(ride.route_number for ride in result.option.transfer.bus_rides) == ("201", "211")
    assert len(result.option.transfer.transfer_walks) == 1
    assert result.option.duration_minutes == 75
    assert result.option.cost_min_krw == 2300
    assert result.option.cost_max_krw == 2400


def test_transfer_rejects_different_patterns_with_same_public_route_number() -> None:
    """provider pattern ID가 달라도 공개 노선번호가 같으면 유효 환승으로 취급하지 않아야 한다."""

    first = (
        "trip-first",
        "route-first",
        "provider-route-first",
        "722-2",
    )
    renamed_same_route = (
        "trip-second",
        "route-second",
        "provider-route-second",
        "722-2",
    )
    genuinely_different_route = (
        "trip-third",
        "route-third",
        "provider-route-third",
        "722-1",
    )

    assert TmapDoorRoutePlanner._different_bus_services(first, renamed_same_route) is False
    assert TmapDoorRoutePlanner._different_bus_services(first, genuinely_different_route) is True
