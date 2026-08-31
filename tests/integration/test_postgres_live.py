"""선택적으로 실행하는 실제 PostGIS 역할 분리 테스트."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from jeju_trip.infrastructure.migrations import (
    MigrationChecksumMismatch,
    run_migrations,
)
from jeju_trip.infrastructure.projection_publisher import (
    PostgresProjectionPublisher,
    SourceCoverageRecord,
)
from jeju_trip.infrastructure.public_data_normalizers import HolidayRecord, JejuBoundaryRecord
from jeju_trip.infrastructure.raw_store import RawObjectPointer
from jeju_trip.infrastructure.source_admin_repository import PostgresSourceAdminRepository
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.planning.policy import load_bus_fare_policy

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    not os.getenv("JEJU_TEST_ADMIN_DSN"),
    reason="로컬 PostGIS DSN이 제공된 경우에만 실행합니다.",
)
def test_live_runtime_role_cannot_read_source_admin() -> None:
    """실제 DB에서 runtime 역할은 travel_read만 읽고 source_admin 조회는 거부되어야 한다."""

    dsn = os.environ["JEJU_TEST_ADMIN_DSN"]
    with psycopg.connect(dsn) as connection:
        connection.execute("SET ROLE jeju_runtime")
        connection.execute("SELECT count(*) FROM travel_read.active_place").fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT count(*) FROM source_admin.data_source").fetchone()


@pytest.mark.skipif(
    not os.getenv("JEJU_TEST_ADMIN_DSN"),
    reason="로컬 PostGIS DSN이 제공된 경우에만 실행합니다.",
)
def test_live_applied_migration_checksum_change_is_rejected(tmp_path: Path) -> None:
    """적용된 migration과 같은 버전의 checksum이 바뀌면 실행을 거부해야 한다."""

    original = (ROOT / "db/migrations/0001_initial.sql").read_text()
    (tmp_path / "0001_changed.sql").write_text(original + "\n-- changed\n")
    with pytest.raises(MigrationChecksumMismatch, match="0001"):
        run_migrations(os.environ["JEJU_TEST_ADMIN_DSN"], tmp_path)


@pytest.mark.skipif(
    not os.getenv("JEJU_TEST_ADMIN_DSN"),
    reason="로컬 PostGIS DSN이 제공된 경우에만 실행합니다.",
)
def test_live_importer_registers_verified_raw_once_and_advances_state() -> None:
    """실제 DB importer는 검증 raw를 한 번만 등록하고 ACQUIRED를 VALIDATED로 전환해야 한다."""

    suffix = uuid4().hex
    base = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tourapi.place")
    contract = base.model_copy(update={"id": f"integration.source-{suffix}"})
    checksum = hashlib.sha256(suffix.encode()).hexdigest()
    pointer = RawObjectPointer(
        source_id=contract.id,
        checksum=checksum,
        bucket="jeju-private-raw",
        object_key=f"raw/v1/{contract.id}/{checksum[:2]}/{checksum}.json",
        object_version_id="integration-v1",
        byte_length=2,
        content_type="application/json",
    )
    repository = PostgresSourceAdminRepository(
        os.environ["JEJU_TEST_ADMIN_DSN"], assume_role="jeju_importer"
    )
    first = repository.register_verified_raw(contract, pointer, "OBSERVED_AT", None, None, 1)
    second = repository.register_verified_raw(contract, pointer, "OBSERVED_AT", None, None, 1)
    assert first.created is True
    assert second.created is False
    assert second.acquisition_id == first.acquisition_id
    repository.mark_validated(first.acquisition_id, 1, 0, "a" * 64)
    with psycopg.connect(os.environ["JEJU_TEST_ADMIN_DSN"]) as connection:
        status = connection.execute(
            "SELECT status FROM source_admin.acquisition WHERE acquisition_id = %s",
            (first.acquisition_id,),
        ).fetchone()
    assert status == ("VALIDATED",)


@pytest.mark.skipif(
    not os.getenv("JEJU_TEST_ADMIN_DSN"),
    reason="로컬 PostGIS DSN이 제공된 경우에만 실행합니다.",
)
def test_live_holiday_publication_is_atomic_and_active() -> None:
    """실제 DB에서 검증된 공휴일 record는 publication과 active pointer에 함께 발행되어야 한다."""

    suffix = uuid4().hex
    dsn = os.environ["JEJU_TEST_ADMIN_DSN"]
    base = SourceCatalog.load(ROOT / "config/data_sources.toml").require("holiday.special-day")
    contract = base
    checksum = hashlib.sha256(f"holiday-{suffix}".encode()).hexdigest()
    pointer = RawObjectPointer(
        source_id=contract.id,
        checksum=checksum,
        bucket="jeju-private-raw",
        object_key=f"raw/v1/{contract.id}/{checksum[:2]}/{checksum}.zip",
        object_version_id="integration-v1",
        byte_length=1,
        content_type="application/zip",
    )
    repository = PostgresSourceAdminRepository(dsn, assume_role="jeju_importer")
    acquisition = repository.register_verified_raw(
        contract, pointer, "SOURCE_DATE", date(2026, 8, 15), None, 1
    )
    repository.mark_validated(acquisition.acquisition_id, 1, 0, "b" * 64)
    publisher = PostgresProjectionPublisher(dsn, assume_role="jeju_importer")
    publication = publisher.publish_holidays(
        acquisition.acquisition_id,
        (
            HolidayRecord(
                fact_id=f"holiday.special-day:20260815:{suffix}",
                holiday_date=date(2026, 8, 15),
                name="광복절",
                is_public_institution_holiday=True,
                date_kind="01",
            ),
        ),
    )
    publisher.publish_coverage(
        publication.publication_id,
        (SourceCoverageRecord("holiday_calendar_ready", 1.0),),
    )
    with psycopg.connect(dsn) as connection:
        active = connection.execute(
            """SELECT count(*) FROM travel_read.active_holiday
               WHERE publication_id = %s""",
            (publication.publication_id,),
        ).fetchone()
    assert active == (1,)


@pytest.mark.skipif(
    not os.getenv("JEJU_TEST_ADMIN_DSN"),
    reason="로컬 PostGIS DSN이 제공된 경우에만 실행합니다.",
)
def test_live_predata_boundary_and_bus_fare_projections_are_queryable() -> None:
    """신규 제주 경계와 버스요금 policy fact는 실제 PostGIS active view에서 조회돼야 한다."""

    dsn = os.environ["JEJU_TEST_ADMIN_DSN"]
    catalog = SourceCatalog.load(ROOT / "config/data_sources.toml")
    repository = PostgresSourceAdminRepository(dsn, assume_role="jeju_importer")
    publisher = PostgresProjectionPublisher(dsn, assume_role="jeju_importer")

    def acquisition(source_id: str, checksum_seed: str):
        contract = catalog.require(source_id)
        checksum = hashlib.sha256(f"{contract.id}-{uuid4().hex}".encode()).hexdigest()
        pointer = RawObjectPointer(
            source_id=contract.id,
            checksum=checksum,
            bucket="jeju-private-raw",
            object_key=f"raw/v1/{contract.id}/{checksum[:2]}/{checksum}.json",
            object_version_id="integration-v1",
            byte_length=2,
            content_type="application/json",
        )
        registered = repository.register_verified_raw(
            contract, pointer, "SOURCE_DATE", date(2026, 8, 10), None, 1
        )
        repository.mark_validated(registered.acquisition_id, 1, 0, checksum)
        return registered.acquisition_id

    boundary = publisher.publish_boundary(
        acquisition("spatial.jeju-boundary", "boundary"),
        (
            JejuBoundaryRecord(
                fact_id="spatial.jeju-boundary:integration",
                boundary_id="integration",
                name="제주특별자치도",
                geometry={
                    "type": "MultiPolygon",
                    "coordinates": [[[[126.0, 33.0], [127.0, 33.0], [127.0, 33.7], [126.0, 33.0]]]],
                },
                source_reference="https://official.example/boundary",
            ),
        ),
    )
    bus_fare = publisher.publish_bus_fare_policy(
        acquisition("jeju.bus-fare-policy", "bus-fare"),
        (load_bus_fare_policy(ROOT / "config/policies/jeju_bus_fare_2026-08-10.toml"),),
    )
    publisher.publish_coverage(
        boundary.publication_id,
        (SourceCoverageRecord("service_area_ready", 1.0),),
    )
    publisher.publish_coverage(
        bus_fare.publication_id,
        (SourceCoverageRecord("fare_policy_ready", 1.0),),
    )
    with psycopg.connect(dsn) as connection:
        boundary_count = connection.execute(
            "SELECT count(*) FROM travel_read.active_service_area_boundary "
            "WHERE publication_id = %s",
            (boundary.publication_id,),
        ).fetchone()
        fare_count = connection.execute(
            "SELECT count(*) FROM travel_read.active_bus_fare_policy WHERE publication_id = %s",
            (bus_fare.publication_id,),
        ).fetchone()
    assert boundary_count == (1,)
    assert fare_count == (2,)
