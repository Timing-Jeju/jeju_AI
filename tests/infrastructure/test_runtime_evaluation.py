"""운영용 일정 판정 evidence의 요청 내부 캐시 테스트."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from jeju_trip.infrastructure.runtime_evaluation import PostgresEvaluationEvidence
from jeju_trip.planning.evaluation import RouteEvidence

KST = timezone(timedelta(hours=9))


def test_route_evidence_is_reused_within_one_evaluation_request(monkeypatch) -> None:
    """원본과 남은 일정의 동일 구간은 요청 안에서 경로 evidence를 다시 계산하지 않아야 한다."""

    evidence = object.__new__(PostgresEvaluationEvidence)
    evidence._request = cast(
        Any, SimpleNamespace(transport=SimpleNamespace(allowed_modes={"taxi"}))
    )
    evidence._route_facts = {}
    calls = 0
    expected = RouteEvidence(
        mode="taxi",
        duration_minutes=20,
        distance_meters=10_000,
        cost_krw=18_000,
        walking_minutes=0,
        transfers=0,
        evidence_fact_ids=("fact-route",),
    )

    def load(*args):
        nonlocal calls
        calls += 1
        return expected

    monkeypatch.setattr(evidence, "_load_route_for_mode", load)
    departure_at = datetime(2026, 8, 14, 9, tzinfo=KST)

    assert evidence.route_for_mode("place-a", "place-b", departure_at, "taxi") is expected
    assert evidence.route_for_mode("place-a", "place-b", departure_at, "taxi") is expected
    assert calls == 1


def test_opening_windows_are_reused_within_one_evaluation_request(monkeypatch) -> None:
    """같은 장소와 날짜의 운영시간은 원본과 남은 일정에서 DB를 한 번만 조회해야 한다."""

    statements: list[str] = []

    class Result:
        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            statements.append(str(statement))
            return Result()

    connections = 0

    def connect(dsn):
        nonlocal connections
        connections += 1
        return Connection()

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_evaluation.psycopg.connect",
        connect,
    )
    evidence = object.__new__(PostgresEvaluationEvidence)
    evidence._runtime_dsn = "postgresql://runtime"
    evidence._opening_facts = {}
    evidence._last_admissions = {}
    evidence._opening_windows_cache = {}
    on_date = date(2026, 8, 14)

    assert evidence.opening_windows("place-a", on_date) == ()
    assert evidence.opening_windows("place-a", on_date) == ()
    assert connections == 1
    assert len(statements) == 2
    assert any("active_place_weekly_closure" in statement for statement in statements)
    assert all("metadata.source_date" in statement for statement in statements)
    assert all("metadata.observed_at IS NULL" in statement for statement in statements)


def test_weekly_closure_returns_a_verified_closed_window(monkeypatch) -> None:
    """여행일의 반복 휴무 fact는 운영시간 미확인이 아니라 검증된 휴무 창으로 반환해야 한다."""

    observed_at = datetime(2026, 8, 24, 9, tzinfo=UTC)

    class Result:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            if "active_place_weekly_closure" in str(statement):
                return Result(
                    (
                        "weekly-closed-tuesday",
                        "CLOSED",
                        None,
                        None,
                        0,
                        "publication-hours",
                        "tourapi.place-intro",
                        "한국관광공사",
                        "hours-v11",
                        None,
                        observed_at,
                    )
                )
            return Result(rows=[])

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_evaluation.psycopg.connect",
        lambda dsn: Connection(),
    )
    evidence = object.__new__(PostgresEvaluationEvidence)
    evidence._runtime_dsn = "postgresql://runtime"
    evidence._opening_facts = {}
    evidence._sources = {}
    evidence._last_admissions = {}
    evidence._opening_windows_cache = {}
    on_date = date(2026, 8, 25)

    result = evidence.opening_windows("place-a", on_date)

    day_start = datetime.combine(on_date, datetime.min.time(), tzinfo=KST)
    assert result == ((day_start, day_start, ("weekly-closed-tuesday",)),)
    assert "weekly-closed-tuesday" in evidence._opening_facts


def test_special_hours_still_subtract_verified_break_periods(monkeypatch) -> None:
    """특별 영업시간이 주간 OPEN을 대체해도 같은 날의 검증 휴게 구간은 유지해야 한다."""

    observed_at = datetime(2026, 8, 30, 9, tzinfo=UTC)
    on_date = date(2026, 8, 31)

    class Result:
        def __init__(self, *, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            if "active_place_schedule_exception" in str(statement):
                return Result(
                    row=(
                        "special-fact",
                        "SPECIAL_HOURS",
                        10 * 60,
                        17 * 60,
                        0,
                        "publication-hours",
                        "tourapi.place-intro",
                        "한국관광공사",
                        "hours-v12",
                        on_date,
                        observed_at,
                    )
                )
            return Result(
                rows=[
                    (
                        "break-fact",
                        12 * 60,
                        13 * 60,
                        0,
                        "publication-hours",
                        "tourapi.place-intro",
                        "한국관광공사",
                        "hours-v12",
                        on_date,
                        observed_at,
                        None,
                        "BREAK",
                    )
                ]
            )

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_evaluation.psycopg.connect",
        lambda dsn: Connection(),
    )
    evidence = object.__new__(PostgresEvaluationEvidence)
    evidence._runtime_dsn = "postgresql://runtime"
    evidence._opening_facts = {}
    evidence._sources = {}
    evidence._last_admissions = {}
    evidence._opening_windows_cache = {}

    result = evidence.opening_windows("place-a", on_date)

    day_start = datetime.combine(on_date, datetime.min.time(), tzinfo=KST)
    assert result == (
        (
            day_start + timedelta(hours=10),
            day_start + timedelta(hours=12),
            ("special-fact", "break-fact"),
        ),
        (
            day_start + timedelta(hours=13),
            day_start + timedelta(hours=17),
            ("special-fact", "break-fact"),
        ),
    )


def test_opening_windows_subtract_verified_break_periods(monkeypatch) -> None:
    """판정 운영창은 검증된 BREAK fact를 OPEN 구간에서 빼고 휴게 중 방문을 허용하지 않아야 한다."""

    observed_at = datetime(2026, 8, 30, 9, tzinfo=UTC)
    on_date = date(2026, 8, 31)

    class Result:
        def __init__(self, *, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters):
            if "active_place_schedule_exception" in str(statement):
                return Result(row=None)
            common = (
                "publication-hours",
                "tourapi.place-intro",
                "한국관광공사",
                "hours-v12",
                on_date,
                observed_at,
            )
            return Result(
                rows=[
                    ("open-fact", 9 * 60, 18 * 60, 0, *common, None, "OPEN"),
                    ("break-fact", 12 * 60, 13 * 60, 0, *common, None, "BREAK"),
                ]
            )

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_evaluation.psycopg.connect",
        lambda dsn: Connection(),
    )
    evidence = object.__new__(PostgresEvaluationEvidence)
    evidence._runtime_dsn = "postgresql://runtime"
    evidence._opening_facts = {}
    evidence._sources = {}
    evidence._last_admissions = {}
    evidence._opening_windows_cache = {}

    result = evidence.opening_windows("place-a", on_date)

    day_start = datetime.combine(on_date, datetime.min.time(), tzinfo=KST)
    assert result == (
        (
            day_start + timedelta(hours=9),
            day_start + timedelta(hours=12),
            ("open-fact", "break-fact"),
        ),
        (
            day_start + timedelta(hours=13),
            day_start + timedelta(hours=18),
            ("open-fact", "break-fact"),
        ),
    )


def test_bus_route_evidence_preserves_transfer_distance(monkeypatch) -> None:
    """버스 재검증도 선택 경로의 거리 값을 보존해 전체 일정을 검증 불가로 만들지 않아야 한다."""

    departure_at = datetime(2026, 8, 18, 9, tzinfo=KST)
    arrival_at = departure_at + timedelta(minutes=30)
    first_ride = SimpleNamespace(
        route_number="370",
        route_id="provider-route-370",
        canonical_boarding_stop_id="stop-a",
        canonical_alighting_stop_id="transfer-stop",
        scheduled_departure_at=departure_at + timedelta(minutes=10),
        scheduled_arrival_at=departure_at + timedelta(minutes=20),
    )
    last_ride = SimpleNamespace(
        route_number="1111",
        route_id="provider-route-1111",
        canonical_boarding_stop_id="transfer-stop",
        canonical_alighting_stop_id="stop-b",
        scheduled_departure_at=departure_at + timedelta(minutes=25),
        scheduled_arrival_at=arrival_at,
    )
    transfer = SimpleNamespace(
        mode="bus",
        direct_walk=None,
        access_walk=SimpleNamespace(planned_minutes=3),
        bus_rides=(first_ride, last_ride),
        taxi_alternative=None,
        distance_meters=1_234,
    )
    option = SimpleNamespace(
        transfer=transfer,
        duration_minutes=35,
        cost_max_krw=1_150,
        cost_min_krw=1_150,
        walking_minutes=8,
        walking_distance_meters=600,
        transfers=1,
        evidence_fact_ids=("fact-bus",),
    )
    evidence = object.__new__(PostgresEvaluationEvidence)
    untyped_evidence = cast(Any, evidence)
    untyped_evidence._gateway = SimpleNamespace(route=lambda *args: option)
    untyped_evidence._routing_budget = SimpleNamespace()
    monkeypatch.setattr(evidence, "_routing_request", lambda mode: object())

    result = evidence._load_route_for_mode("place-a", "place-b", departure_at, "bus")

    assert result is not None
    assert result.distance_meters == 1_234
    assert result.provider_route_id == "provider-route-370"
    assert result.boarding_stop_id == "stop-a"
    assert result.alighting_stop_id == "stop-b"
    assert result.scheduled_departure_at == departure_at + timedelta(minutes=10)
    assert result.scheduled_arrival_at == arrival_at
