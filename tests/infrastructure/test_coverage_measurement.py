"""projection 기반 capability coverage 계산 테스트."""

from uuid import uuid4

import pytest

from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer, coverage_ratio


class _SingleRowResult:
    def __init__(self, row: tuple[int, int]) -> None:
        self._row = row

    def fetchone(self) -> tuple[int, int]:
        return self._row


class _StopCoverageConnection:
    def __init__(self) -> None:
        self.query = ""
        self.parameters: tuple[object, ...] = ()
        self.result: tuple[int, int] = (2, 3)

    def execute(self, query: object, parameters: tuple[object, ...]) -> _SingleRowResult:
        self.query = str(query)
        self.parameters = parameters
        return _SingleRowResult(self.result)


def test_coverage_ratio_requires_nonempty_complete_measurement() -> None:
    """coverage는 분모가 있고 모든 대상이 충족될 때만 readiness 가능한 1이어야 한다."""

    assert coverage_ratio(0, 0) == (0.0, "COVERAGE_DENOMINATOR_EMPTY")
    assert coverage_ratio(8, 10) == (0.8, "COVERAGE_INCOMPLETE")
    assert coverage_ratio(10, 10) == (1.0, None)


def test_airport_coverage_uses_importer_projection_permissions() -> None:
    """공항 게시 검증은 runtime 전용 뷰 권한을 추가하지 않고 수행한다."""
    connection = _StopCoverageConnection()
    connection.result = (1, 1)
    assert PostgresCoverageMeasurer._counts(
        connection, uuid4(), "kac.airport", "airport_anchor_ready", "JEJU_ALL", "ALL"
    ) == (1, 1)
    assert "travel_read" not in connection.query
    assert "source_admin.active_snapshot" in connection.query
    assert "ST_Covers" in connection.query


def test_coverage_ratio_rejects_orphan_numerator_instead_of_clamping() -> None:
    """분모보다 큰 충족 건수는 100%로 숨기지 않고 측정 무결성 오류로 거부해야 한다."""

    with pytest.raises(ValueError, match="COVERAGE_MEASUREMENT_INVALID"):
        coverage_ratio(11, 10)


def test_confirmed_stop_coverage_uses_required_scope_stops_as_denominator() -> None:
    """정류장 mapping coverage는 동부 scope의 필수 STOP만 분모로 세어야 한다."""

    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "transport.stop-identity-map",
        "confirmed_stop_mapping_ready",
        "JEJU_EAST",
        "POC_V1",
    )

    assert measured == (2, 3)
    assert "member_type = 'STOP'" in connection.query
    assert "mapping_status = 'CONFIRMED'" in connection.query
    assert connection.parameters[:2] == ("JEJU_EAST", "POC_V1")


def test_global_confirmed_stop_coverage_uses_every_active_official_stop() -> None:
    """전역 정류장 mapping coverage는 별도 scope manifest 대신 모든 active TAGO stop을 센다."""

    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.result = (4_273, 4_273)
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "transport.stop-identity-map",
        "confirmed_stop_mapping_ready",
        "JEJU_ALL",
        "ALL",
    )

    assert measured == (4_273, 4_273)
    assert "FROM travel_projection.bus_stop_fact" in connection.query
    assert "JOIN source_admin.active_snapshot" in connection.query
    assert "source_fact_id AS member_id" in connection.query
    assert connection.parameters == (UUID("00000000-0000-0000-0000-000000000001"),)


def test_route_catalog_coverage_requires_a_nonempty_unique_publication() -> None:
    """TAGO 노선 catalog는 비어 있지 않고 fact ID가 유일할 때만 준비 완료여야 한다."""

    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.execute = lambda query, parameters: _SingleRowResult((1, 1))  # type: ignore[method-assign]

    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "tago.bus-route",
        "bus_route_catalog_ready",
        "JEJU_ALL",
        "ALL",
    )

    assert measured == (1, 1)


def test_route_stop_coverage_uses_the_active_route_catalog_as_denominator() -> None:
    """전역 경유정류장 coverage는 활성 TAGO 노선 각각의 존재 여부를 분모로 계산해야 한다."""

    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "tago.bus-route-stops",
        "bus_route_stop_catalog_ready",
        "JEJU_ALL",
        "ALL",
    )

    assert measured == (2, 3)
    assert "active_snapshot" in connection.query
    assert "bus_route_fact" in connection.query
    assert "bus_route_stop" in connection.query


@pytest.mark.parametrize(
    ("source_id", "capability", "measured", "expected"),
    [
        pytest.param(
            "jeju.bus-fare-policy",
            "bus_fare_policy_ready",
            2,
            (2, 2),
            id="버스-두-운임등급",
        ),
        pytest.param(
            "jeju.taxi-fare-policy",
            "taxi_fare_policy_ready",
            1,
            (1, 1),
            id="택시-일반형",
        ),
    ],
)
def test_fare_coverage_requires_policy_valid_for_trip_date(
    source_id: str, capability: str, measured: int, expected: tuple[int, int]
) -> None:
    """교통 운임 coverage는 여행일에 유효한 버스·일반택시 정책만 인정해야 한다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.execute = lambda query, parameters: _SingleRowResult((measured, measured))  # type: ignore[method-assign]
    result = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        source_id,
        capability,
        "JEJU_EAST",
        "POC_V1",
        date(2026, 8, 15),
        date(2026, 8, 15),
    )

    assert result == expected


def test_global_future_bus_coverage_uses_all_active_routes_and_exact_date() -> None:
    """전역 미래 버스 coverage는 발행 trip 자체가 아니라 active 공식 노선 전체를 분모로 센다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.result = (313, 974)
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "jeju.bus-timetable",
        "future_bus_planning_ready",
        "JEJU_ALL",
        "ALL",
        date(2026, 8, 18),
        date(2026, 8, 18),
    )

    assert measured == (313, 974)
    assert "FROM travel_projection.bus_route_fact" in connection.query
    assert "route.source_id = 'tago.bus-route'" in connection.query
    assert "calendar.day_type = requested.day_type" in connection.query
    assert "service_calendar_exception" in connection.query
    assert "count(stop.fact_id) >= 2" in connection.query
    assert connection.parameters == (
        date(2026, 8, 18),
        UUID("00000000-0000-0000-0000-000000000001"),
    )


def test_scoped_future_bus_coverage_filters_exact_service_day() -> None:
    """권역 시간표 coverage도 요청일 요일·유효기간·제외일을 통과한 운행편만 센다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "jeju.bus-timetable",
        "future_bus_planning_ready",
        "JEJU_EAST",
        "POC_V1",
        date(2026, 8, 18),
        date(2026, 8, 18),
    )

    assert measured == (2, 3)
    assert "calendar.day_type = requested.day_type" in connection.query
    assert "calendar.starts_on <= requested.trip_date" in connection.query
    assert "service_calendar_exception" in connection.query
    assert "matched_stop_count = stop_count" in connection.query
    assert "WHERE EXISTS" in connection.query
    assert connection.parameters == (
        date(2026, 8, 18),
        UUID("00000000-0000-0000-0000-000000000001"),
    )


def test_opening_hours_coverage_excludes_accommodation_boundary() -> None:
    """숙소는 일정 경계이므로 활동 운영시간 coverage 분모에 포함하지 않아야 한다."""

    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "travel.place-hours-map",
        "opening_hours_ready",
        "JEJU_EAST",
        "POC_V1",
    )

    assert "role <> 'accommodation'" in connection.query


def test_global_opening_hours_coverage_uses_active_non_accommodation_places() -> None:
    """전역 운영시간은 숙박을 뺀 active 장소와 active·후보 근거 합집합을 날짜별로 세어야 한다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.result = (847, 959)
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "tourapi.place-intro",
        "opening_hours_ready",
        "JEJU_ALL",
        "ALL",
        date(2026, 8, 24),
        date(2026, 8, 24),
    )

    assert measured == (847, 959)
    assert "place.source_id = 'tourapi.place'" in connection.query
    assert "place.attributes->>'content_type_id' <> '32'" in connection.query
    assert "JOIN source_admin.active_snapshot" in connection.query
    assert "rule.publication_id = %s" in connection.query
    assert "active_publication.source_id <> %s" in connection.query
    assert "exception.exception_type IN ('CLOSED', 'SPECIAL_HOURS')" in connection.query
    assert "travel_projection.place_weekly_closure" in connection.query
    assert connection.parameters == (
        UUID("00000000-0000-0000-0000-000000000001"),
        "tourapi.place-intro",
        date(2026, 8, 24),
        date(2026, 8, 24),
        date(2026, 8, 24),
        date(2026, 8, 24),
        date(2026, 8, 24),
        date(2026, 8, 24),
        UUID("00000000-0000-0000-0000-000000000001"),
        "tourapi.place-intro",
        date(2026, 8, 24),
        date(2026, 8, 24),
        date(2026, 8, 24),
        UUID("00000000-0000-0000-0000-000000000001"),
        "tourapi.place-intro",
        date(2026, 8, 24),
    )


def test_opening_hours_snapshot_coverage_requires_one_observation_per_active_place() -> None:
    """상세소개 스냅샷은 운영시간 정확성과 별개로 active 장소 전부의 관측 결과를 요구해야 한다."""

    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.result = (1_019, 1_019)
    publication_id = UUID("00000000-0000-0000-0000-000000000001")
    measured = PostgresCoverageMeasurer._counts(
        connection,
        publication_id,
        "tourapi.place-intro",
        "opening_hours_snapshot_ready",
        "JEJU_ALL",
        "ALL",
    )

    assert measured == (1_019, 1_019)
    assert "travel_projection.place_opening_observation" in connection.query
    assert "place.source_id = 'tourapi.place'" in connection.query
    assert "content_type_id' <> '32'" not in connection.query
    assert connection.parameters == (publication_id,)


def test_opening_hours_coverage_requires_rules_valid_on_exact_trip_date() -> None:
    """범위 운영시간 coverage도 규칙·주간 휴무·날짜 예외를 요청 여행일에 맞춰 인정해야 한다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "travel.place-hours-map",
        "opening_hours_ready",
        "JEJU_EAST",
        "POC_V1",
        date(2026, 8, 14),
        date(2026, 8, 14),
    )

    assert "normalization_status = 'VERIFIED'" in connection.query
    assert "EXTRACT(ISODOW" in connection.query
    assert "travel_projection.place_weekly_closure" in connection.query
    assert "travel_projection.place_schedule_exception" in connection.query
    assert "exception.exception_type IN ('CLOSED', 'SPECIAL_HOURS')" in connection.query


def test_entrance_coverage_rejects_unverified_or_expired_candidates() -> None:
    """입구 coverage는 여행일까지 유효한 VERIFIED 후보만 필수 장소 충족으로 세어야 한다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "travel.place-entrance-map",
        "verified_entrances_ready",
        "JEJU_EAST",
        "POC_V1",
        date(2026, 8, 14),
        date(2026, 8, 14),
    )

    assert "verification_status = 'VERIFIED'" in connection.query
    assert "verification_expires_at" in connection.query
    assert connection.parameters[-6:] == (
        date(2026, 8, 14),
        date(2026, 8, 14),
        date(2026, 8, 14),
        date(2026, 8, 14),
        date(2026, 8, 14),
        date(2026, 8, 14),
    )


def test_global_entrance_coverage_uses_every_active_place_without_scope_manifest() -> None:
    """전역 입구 coverage는 PoC scope 없이도 모든 active 장소를 분모로 세어야 한다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.result = (0, 992)
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "travel.place-entrance-map",
        "verified_entrances_ready",
        "JEJU_ALL",
        "ALL",
        date(2026, 8, 31),
        date(2026, 8, 31),
    )

    assert measured == (0, 992)
    assert "FROM travel_projection.place_fact place" in connection.query
    assert "JOIN source_admin.active_snapshot active" in connection.query
    assert "active_service_scope_member" not in connection.query


def test_accessibility_coverage_requires_current_verified_accessible_walk_entrance() -> None:
    """접근성 coverage는 유효한 accessible 보행 입구가 있는 장소만 충족으로 세어야 한다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.result = (3, 12)
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "travel.place-entrance-map",
        "accessibility_ready",
        "JEJU_EAST",
        "POC_V1",
        date(2026, 8, 31),
        date(2026, 8, 31),
    )

    assert measured == (3, 12)
    assert "entrance_type = 'accessible'" in connection.query
    assert "'walk' = ANY(supported_modes)" in connection.query
    assert "verification_status = 'VERIFIED'" in connection.query
    assert "verification_expires_at" in connection.query


def test_restaurant_dietary_coverage_requires_three_current_verified_venues() -> None:
    """식이 조건 자동 식사는 현재 유효한 검증 메뉴를 가진 서로 다른 식당 세 곳을 요구해야 한다."""

    from datetime import date
    from uuid import UUID

    from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer

    connection = _StopCoverageConnection()
    connection.result = (2, 3)
    measured = PostgresCoverageMeasurer._counts(
        connection,
        UUID("00000000-0000-0000-0000-000000000001"),
        "travel.restaurant-dietary-map",
        "restaurant_recommendation_ready",
        "JEJU_ALL",
        "ALL",
        date(2026, 8, 31),
        date(2026, 8, 31),
    )

    assert measured == (2, 3)
    assert "restaurant_dietary_fact" in connection.query
    assert "count(DISTINCT dietary.place_fact_id)" in connection.query
    assert "place.category = '39'" in connection.query
    assert "category_level_3' <> 'A05020900'" in connection.query
    assert "verification_expires_at" in connection.query
