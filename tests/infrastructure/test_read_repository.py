"""active 장소 조회의 제주 경계 판정 테스트."""

from datetime import UTC, date, datetime

from jeju_trip.domain.models import InspectBusStopInput, SearchPlacesInput
from jeju_trip.domain.readiness import CapabilityReason, CapabilityState
from jeju_trip.infrastructure.read_repository import ActiveTravelReadRepository
from tests.factories import make_request


def test_place_search_uses_active_boundary_and_returns_boundary_provenance(monkeypatch) -> None:
    """장소 검색은 ST_Covers를 적용하고 geometry 대신 경계 fact/publication만 반환해야 한다."""

    statements: list[str] = []

    class Result:
        def fetchall(self):
            return [
                (
                    "tourapi.place:1",
                    "제주 장소",
                    "관광지",
                    "제주 주소",
                    33.5,
                    126.5,
                    "tourapi.place",
                    "place-publication",
                    "spatial.jeju-boundary:39:20250630",
                    "spatial.jeju-boundary",
                    "boundary-publication",
                    1,
                )
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            statements.append(statement)
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: Connection(),
    )
    repository = ActiveTravelReadRepository("postgresql://runtime")
    monkeypatch.setattr(
        repository,
        "capability_states",
        lambda *args: {
            "place_search_ready": CapabilityState.available(),
            "service_area_ready": CapabilityState.available(),
        },
    )
    response = repository.search_places(SearchPlacesInput(query="제주"))

    assert "ST_Covers(boundary.geometry, place.position::geometry)" in statements[0]
    assert response.places[0].source_refs[0].source_id == "tourapi.place"
    assert response.places[0].source_refs[1].source_id == "spatial.jeju-boundary"
    assert response.places[0].source_refs[1].publication_id == "boundary-publication"
    assert "geometry" not in response.model_dump_json()


def test_place_search_reports_data_unavailable_when_source_is_stale(monkeypatch) -> None:
    """장소나 제주 경계 source가 stale이면 빈 정상 검색이 아니라 데이터 부족을 반환해야 한다."""

    repository = ActiveTravelReadRepository("postgresql://runtime")
    monkeypatch.setattr(
        repository,
        "capability_states",
        lambda *args: {
            "place_search_ready": CapabilityState.unavailable(CapabilityReason.STALE),
            "service_area_ready": CapabilityState.available(),
        },
    )
    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: (_ for _ in ()).throw(
            AssertionError("stale 검색은 DB fact를 읽으면 안 됩니다.")
        ),
    )

    response = repository.search_places(SearchPlacesInput(query="제주"))

    assert response.status == "data_unavailable"
    assert response.reason_code == "PLACE_SEARCH_SOURCE_STALE"


def test_bus_stop_inspection_reports_data_unavailable_when_source_is_stale(monkeypatch) -> None:
    """정류장 또는 mapping source가 stale이면 active 행이 남아 있어도 조회하지 않아야 한다."""

    repository = ActiveTravelReadRepository("postgresql://runtime")
    monkeypatch.setattr(
        repository,
        "source_state",
        lambda source_id: (
            CapabilityState.unavailable(CapabilityReason.STALE)
            if source_id == "tago.bus-stop"
            else CapabilityState.available()
        ),
    )
    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: (_ for _ in ()).throw(
            AssertionError("stale 정류장은 DB fact를 읽으면 안 됩니다.")
        ),
    )

    response = repository.inspect_bus_stop(InspectBusStopInput(stop_id="stop-1"))

    assert response.status == "data_unavailable"
    assert response.reason_code == "BUS_STOP_SOURCE_STALE"


def test_bus_stop_inspection_returns_routes_and_canonical_source_refs(monkeypatch) -> None:
    """정류장 조회는 운행 노선과 정류장·identity mapping의 승인 source를 반환해야 한다."""

    class Result:
        def fetchone(self):
            return (
                "canonical-stop-1",
                "provider-stop-1",
                "제주 정류장",
                "동쪽",
                33.5,
                126.5,
                "OFFICIAL_ID",
                "CONFIRMED",
                ["201", "202"],
                "tago.bus-stop",
                "stop-publication",
                "stop-fact-1",
                "transport.stop-identity-map",
                "identity-publication",
                "identity-fact-1",
            )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            return Result()

    repository = ActiveTravelReadRepository("postgresql://runtime")
    monkeypatch.setattr(repository, "source_state", lambda source_id: CapabilityState.available())
    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: Connection(),
    )

    response = repository.inspect_bus_stop(InspectBusStopInput(stop_id="canonical-stop-1"))

    assert response.status == "success"
    assert response.route_numbers == ("201", "202")
    assert {source.source_id for source in response.source_refs} == {
        "tago.bus-stop",
        "transport.stop-identity-map",
    }


def test_capability_flags_inherit_only_global_boundary_and_place_catalog(monkeypatch) -> None:
    """동부 readiness는 전역 경계·장소만 상속하고 나머지는 exact scope로 조회해야 한다."""

    statements: list[str] = []

    class Result:
        def fetchall(self):
            observed_at = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
            return [
                (
                    "service_area_ready",
                    True,
                    "spatial.jeju-boundary",
                    date(2026, 6, 30),
                    None,
                    observed_at,
                ),
                (
                    "place_search_ready",
                    True,
                    "tourapi.place",
                    None,
                    observed_at,
                    observed_at,
                ),
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            statements.append(statement)
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: Connection(),
    )
    flags = ActiveTravelReadRepository(
        "postgresql://runtime",
        now=lambda: datetime(2026, 8, 15, 0, 0, tzinfo=UTC),
    ).capability_flags("2026-08-15", "JEJU_EAST", "POC_V1")

    assert flags == {"service_area_ready": True, "place_search_ready": True}
    assert "capability IN ('service_area_ready', 'place_search_ready')" in statements[0]
    assert "capability NOT IN ('service_area_ready', 'place_search_ready')" in statements[0]


def test_capability_flags_close_stale_place_snapshot(monkeypatch) -> None:
    """활성 coverage가 1이어도 장소 source가 계약 기한을 넘겼으면 readiness를 닫아야 한다."""

    class Result:
        def fetchall(self):
            return [
                (
                    "place_search_ready",
                    True,
                    "tourapi.place",
                    None,
                    datetime(2026, 8, 11, 1, 38, tzinfo=UTC),
                    datetime(2026, 8, 11, 1, 39, tzinfo=UTC),
                )
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: Connection(),
    )
    repository = ActiveTravelReadRepository(
        "postgresql://runtime",
        now=lambda: datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
    )

    assert repository.capability_flags("2026-08-30") == {"place_search_ready": False}


def test_capability_states_split_legacy_fares_and_preserve_stale_reason(monkeypatch) -> None:
    """기존 fare coverage도 source별로 분리하고 stale 원인을 capability 상태에 보존해야 한다."""

    class Result:
        def fetchall(self):
            return [
                (
                    "fare_policy_ready",
                    1.0,
                    None,
                    "JEJU_ALL",
                    "ALL",
                    date(2026, 8, 15),
                    date(2026, 8, 15),
                    "jeju.bus-fare-policy",
                    date(2026, 8, 10),
                    None,
                    None,
                ),
                (
                    "fare_policy_ready",
                    1.0,
                    None,
                    "JEJU_ALL",
                    "ALL",
                    date(2026, 8, 15),
                    date(2026, 8, 15),
                    "jeju.taxi-fare-policy",
                    date(2024, 7, 1),
                    None,
                    None,
                ),
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement):
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: Connection(),
    )
    repository = ActiveTravelReadRepository(
        "postgresql://runtime",
        now=lambda: datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
    )

    states = repository.capability_states(date(2026, 8, 15))

    assert states["bus_fare_policy_ready"] == CapabilityState.available()
    assert states["taxi_fare_policy_ready"] == CapabilityState.unavailable(
        CapabilityReason.STALE
    )


def test_capability_states_distinguish_incomplete_scope_from_stale_source(monkeypatch) -> None:
    """fresh publication의 요청 scope 미포함은 stale이 아니라 coverage 미완료로 판정해야 한다."""

    class Result:
        def fetchall(self):
            return [
                (
                    "future_bus_planning_ready",
                    1.0,
                    None,
                    "JEJU_WEST",
                    "POC_V1",
                    date(2026, 8, 15),
                    date(2026, 8, 15),
                    "jeju.bus-timetable",
                    date(2026, 8, 24),
                    None,
                    None,
                )
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement):
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: Connection(),
    )
    repository = ActiveTravelReadRepository(
        "postgresql://runtime",
        now=lambda: datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
    )

    state = repository.capability_states(
        date(2026, 8, 15), "JEJU_EAST", "POC_V1"
    )["future_bus_planning_ready"]

    assert state == CapabilityState.unavailable(CapabilityReason.COVERAGE_INCOMPLETE)


def test_exact_bus_probe_requires_confirmed_service_day_stop_near_every_request_place(
    monkeypatch,
) -> None:
    """요청 단위 버스 준비도는 모든 요청 장소 주변의 confirmed exact 정류장을 요구해야 한다."""

    captured: list[tuple[str, dict]] = []

    class Result:
        def fetchone(self):
            return (True,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            captured.append((statement, parameters))
            return Result()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.read_repository.psycopg.connect",
        lambda dsn: Connection(),
    )
    request = make_request().model_copy(
        update={
            "accommodation": make_request().accommodation.model_copy(
                update={"place_id": "tourapi.place:hotel"}
            )
        }
    )

    ready = ActiveTravelReadRepository(
        "postgresql://runtime"
    ).exact_bus_planning_available(request)

    assert ready is True
    query, parameters = captured[0]
    assert "active_scheduled_trip" in query
    assert "active_stop_time" in query
    assert "mapping_status = 'CONFIRMED'" in query
    assert "ST_DWithin(stop.position, endpoint.position, 2500)" in query
    assert parameters["place_ids"] == ["tourapi.place:hotel"]
