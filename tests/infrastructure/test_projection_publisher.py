"""projection publication 보조 계약 테스트."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from jeju_trip.infrastructure.projection_publisher import (
    PostgresProjectionPublisher,
    SourceCoverageRecord,
    validate_activation_coverage,
    validate_coverage_source,
)


def test_source_coverage_requires_bounded_ratio_and_date_range() -> None:
    """capability coverage는 0~1 비율과 올바른 서비스 날짜 범위만 허용해야 한다."""

    with pytest.raises(ValueError, match="COVERAGE_RATIO_INVALID"):
        SourceCoverageRecord("opening_hours_ready", 1.1)
    with pytest.raises(ValueError, match="COVERAGE_DATE_RANGE_INVALID"):
        SourceCoverageRecord(
            "future_bus_planning_ready",
            1,
            service_date_from=date(2026, 8, 16),
            service_date_to=date(2026, 8, 15),
        )


def test_incomplete_coverage_requires_a_blocking_reason() -> None:
    """전역 미달 coverage는 capability가 꺼진 원인을 반드시 기록해야 한다."""

    with pytest.raises(ValueError, match="COVERAGE_BLOCKING_REASON_REQUIRED"):
        SourceCoverageRecord("place_search_ready", 0.75)


def test_coverage_requires_explicit_region_grid_and_complete_date_pair() -> None:
    """coverage는 측정 권역·격자와 완전한 서비스 날짜 범위를 명시해야 한다."""

    with pytest.raises(ValueError, match="COVERAGE_SCOPE_REQUIRED"):
        SourceCoverageRecord("place_search_ready", 1, region_code="")
    with pytest.raises(ValueError, match="COVERAGE_DATE_RANGE_INCOMPLETE"):
        SourceCoverageRecord(
            "future_bus_planning_ready",
            1,
            service_date_from=date(2026, 8, 15),
        )


def test_coverage_capability_must_match_publication_source() -> None:
    """장소 publication이 미래 버스 capability를 임의로 활성화하지 못해야 한다."""

    with pytest.raises(ValueError, match="COVERAGE_SOURCE_MISMATCH"):
        validate_coverage_source(
            "tourapi.place",
            (SourceCoverageRecord("future_bus_planning_ready", 1),),
        )
    validate_coverage_source(
        "tourapi.place",
        (SourceCoverageRecord("place_search_ready", 1),),
    )


def test_airport_coverage_does_not_claim_tourism_catalog_readiness() -> None:
    """공항 한 건을 관광지 전체 준비 상태로 오인하지 않는다."""
    validate_coverage_source("kac.airport", (SourceCoverageRecord("airport_anchor_ready", 1),))
    with pytest.raises(ValueError, match="COVERAGE_SOURCE_MISMATCH"):
        validate_coverage_source("kac.airport", (SourceCoverageRecord("place_search_ready", 1),))


def test_fare_capability_is_bound_to_its_own_policy_source() -> None:
    """버스·택시 요금 readiness는 서로의 policy publication으로 발행할 수 없어야 한다."""

    validate_coverage_source(
        "jeju.bus-fare-policy",
        (SourceCoverageRecord("bus_fare_policy_ready", 1),),
    )
    validate_coverage_source(
        "jeju.taxi-fare-policy",
        (SourceCoverageRecord("taxi_fare_policy_ready", 1),),
    )
    with pytest.raises(ValueError, match="COVERAGE_SOURCE_MISMATCH"):
        validate_coverage_source(
            "jeju.taxi-fare-policy",
            (SourceCoverageRecord("bus_fare_policy_ready", 1),),
        )


def test_tour_intro_activation_separates_complete_snapshot_from_exact_hours() -> None:
    """상세소개는 스냅샷 1.0이면 정확 운영시간 미달을 품질 지표로 함께 보존해 활성화해야 한다."""

    validate_activation_coverage(
        "tourapi.place-intro",
        (
            SourceCoverageRecord("opening_hours_snapshot_ready", 1),
            SourceCoverageRecord(
                "opening_hours_ready",
                637 / 959,
                service_date_from=date(2026, 8, 25),
                service_date_to=date(2026, 8, 25),
                blocking_reason="COVERAGE_INCOMPLETE",
            ),
        ),
    )
    with pytest.raises(ValueError, match="SNAPSHOT_COVERAGE_NOT_READY_FOR_ACTIVATION"):
        validate_activation_coverage(
            "tourapi.place-intro",
            (
                SourceCoverageRecord(
                    "opening_hours_snapshot_ready",
                    0.99,
                    blocking_reason="COVERAGE_INCOMPLETE",
                ),
            ),
        )


def test_restaurant_dietary_readiness_rejects_place_and_hours_sources() -> None:
    """TourAPI 장소·운영시간은 알레르기·제외음식 안전성 capability의 근거가 될 수 없어야 한다."""

    for source_id in ("tourapi.place", "tourapi.place-intro", "travel.place-hours-map"):
        with pytest.raises(ValueError, match="COVERAGE_SOURCE_MISMATCH"):
            validate_coverage_source(
                source_id,
                (SourceCoverageRecord("restaurant_recommendation_ready", 1),),
            )

    validate_coverage_source(
        "travel.restaurant-dietary-map",
        (SourceCoverageRecord("restaurant_recommendation_ready", 1),),
    )


def test_bus_catalog_coverage_matches_only_the_official_route_source() -> None:
    """버스 노선 catalog readiness는 TAGO 노선 publication에만 연결돼야 한다."""

    validate_coverage_source(
        "tago.bus-route",
        (SourceCoverageRecord("bus_route_catalog_ready", 1),),
    )
    with pytest.raises(ValueError, match="COVERAGE_SOURCE_MISMATCH"):
        validate_coverage_source(
            "tago.bus-route-stops",
            (SourceCoverageRecord("bus_route_catalog_ready", 1),),
        )

    validate_coverage_source(
        "tago.bus-route-stops",
        (SourceCoverageRecord("bus_route_stop_catalog_ready", 1),),
    )
    with pytest.raises(ValueError, match="COVERAGE_SOURCE_MISMATCH"):
        validate_coverage_source(
            "tago.bus-route-stops",
            (SourceCoverageRecord("confirmed_stop_mapping_ready", 1),),
        )


def test_active_normalized_dataset_is_reused_without_duplicate_insert(monkeypatch) -> None:
    """동일한 active 정규화 dataset은 새 publication 삽입 없이 NO_CHANGE 상태로 재사용해야 한다."""

    acquisition_id = uuid4()
    publication_id = uuid4()
    published_at = datetime(2026, 8, 18, 9, tzinfo=UTC)
    inserted: list[object] = []

    class Result:
        def __init__(self, row=None, rowcount=0):
            self.row = row
            self.rowcount = rowcount

        def fetchone(self):
            return self.row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters=None):
            query = str(statement)
            if "FROM source_admin.acquisition" in query:
                return Result(
                    (
                        "tago.bus-route",
                        "VALIDATED",
                        "b" * 64,
                        "a" * 64,
                        "v1",
                        "SOURCE_DATE",
                        date(2026, 8, 18),
                        None,
                        published_at,
                    )
                )
            if "pg_advisory_xact_lock" in query:
                return Result()
            if "FROM source_admin.publication publication" in query:
                assert "active_snapshot" not in query
                return Result(
                    (publication_id, "2026-08-18-aaaaaaaaaaaa", published_at)
                )
            if "SET status = 'NO_CHANGE'" in query:
                return Result(rowcount=1)
            raise AssertionError("예상하지 않은 publication query")

    monkeypatch.setattr(
        "jeju_trip.infrastructure.projection_publisher.psycopg.connect",
        lambda dsn: Connection(),
    )
    publisher = PostgresProjectionPublisher("postgresql://importer")

    publication = publisher._publish(
        acquisition_id,
        ("record",),
        lambda connection, basis: inserted.append((connection, basis)),
    )

    assert publication.publication_id == publication_id
    assert publication.dataset_version == "2026-08-18-aaaaaaaaaaaa"
    assert publication.created is False
    assert inserted == []


def test_staged_normalized_dataset_is_reused_without_unique_violation(monkeypatch) -> None:
    """동일한 staged 정규화 dataset도 중복 삽입 없이 기존 publication을 재사용해야 한다."""

    acquisition_id = uuid4()
    publication_id = uuid4()
    published_at = datetime(2026, 8, 24, 9, tzinfo=UTC)
    inserted: list[object] = []

    class Result:
        def __init__(self, row=None, rowcount=0):
            self.row = row
            self.rowcount = rowcount

        def fetchone(self):
            return self.row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, parameters=None):
            query = str(statement)
            if "FROM source_admin.acquisition" in query:
                return Result(
                    (
                        "tourapi.place-intro",
                        "VALIDATED",
                        "b" * 64,
                        "a" * 64,
                        "v8",
                        "OBSERVED_AT",
                        None,
                        published_at,
                        published_at,
                    )
                )
            if "pg_advisory_xact_lock" in query:
                return Result()
            if "FROM source_admin.publication publication" in query:
                assert "active_snapshot" not in query
                return Result(
                    (publication_id, "2026-08-24-aaaaaaaaaaaa", published_at)
                )
            if "SET status = 'NO_CHANGE'" in query:
                return Result(rowcount=1)
            raise AssertionError("예상하지 않은 publication query")

    monkeypatch.setattr(
        "jeju_trip.infrastructure.projection_publisher.psycopg.connect",
        lambda dsn: Connection(),
    )
    publisher = PostgresProjectionPublisher("postgresql://importer")

    publication = publisher._publish(
        acquisition_id,
        ("record",),
        lambda connection, basis: inserted.append((connection, basis)),
    )

    assert publication.publication_id == publication_id
    assert publication.created is False
    assert inserted == []
