"""실제 MinIO와 Postgres를 잇는 raw-first refresh 종단 테스트."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import boto3
import psycopg
import pytest

from jeju_trip.application.manual_import import ManualCsvImportService
from jeju_trip.application.refresh_service import PublicDataRefreshService
from jeju_trip.infrastructure.bus_stop_publisher import PostgresBusStopPublisher
from jeju_trip.infrastructure.projection_publisher import (
    PostgresProjectionPublisher,
    SourceCoverageRecord,
)
from jeju_trip.infrastructure.public_data_http import FetchedJson
from jeju_trip.infrastructure.public_data_normalizers import normalize_tago_stops
from jeju_trip.infrastructure.raw_store import PrivateRawObjectStore
from jeju_trip.infrastructure.s3_object_client import S3ObjectClient
from jeju_trip.infrastructure.source_admin_repository import PostgresSourceAdminRepository
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = (
    "JEJU_TEST_ADMIN_DSN",
    "JEJU_TEST_S3_ENDPOINT",
    "JEJU_TEST_S3_ACCESS_KEY",
    "JEJU_TEST_S3_SECRET_KEY",
)


@pytest.mark.skipif(
    not all(os.getenv(name) for name in REQUIRED),
    reason="로컬 PostGIS와 MinIO 접속정보가 모두 제공된 경우에만 실행합니다.",
)
def test_live_manual_opening_hours_are_raw_first_and_queryable(
    tmp_path: Path,
) -> None:
    """수동 운영시간은 raw와 같은 acquisition으로 발행돼 runtime view에 보여야 한다."""

    rules = tmp_path / "rules.csv"
    rules.write_text(
        "place_fact_id,rule_id,service_day,valid_from,valid_to,period_kind,"
        "opens_at,closes_at,closes_day_offset,last_admission_at,last_order_at,"
        "source_reference\n"
        "tourapi.place:live,live-rule,1,2026-01-01,2026-12-31,OPEN,"
        "09:00,18:00,0,17:30,,https://official.example/live\n",
        encoding="utf-8",
    )
    exceptions = tmp_path / "exceptions.csv"
    exceptions.write_text(
        "place_fact_id,exception_id,exception_date,exception_type,opens_at,"
        "closes_at,closes_day_offset,source_reference\n",
        encoding="utf-8",
    )
    boto_client = boto3.client(
        "s3",
        endpoint_url=os.environ["JEJU_TEST_S3_ENDPOINT"],
        aws_access_key_id=os.environ["JEJU_TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["JEJU_TEST_S3_SECRET_KEY"],
        region_name="ap-northeast-2",
    )
    base = SourceCatalog.load(ROOT / "config/data_sources.toml").require("travel.place-hours-map")
    contract = base
    dsn = os.environ["JEJU_TEST_ADMIN_DSN"]
    publisher = PostgresProjectionPublisher(dsn, assume_role="jeju_importer")
    outcome = ManualCsvImportService(
        PrivateRawObjectStore(S3ObjectClient(boto_client)),
        PostgresSourceAdminRepository(dsn, assume_role="jeju_importer"),
    ).import_opening_hours(
        contract,
        {
            "place-opening-rules.csv": rules,
            "place-schedule-exceptions.csv": exceptions,
        },
        datetime(2026, 8, 10, tzinfo=UTC).date(),
        publisher.publish_opening_hours,
    )
    assert outcome.status == "STAGED"
    assert outcome.publication_id is not None
    publisher.publish_coverage(
        UUID(outcome.publication_id),
        (SourceCoverageRecord("opening_hours_ready", 1.0),),
    )
    with psycopg.connect(dsn) as connection:
        connection.execute("SET ROLE jeju_runtime")
        row = connection.execute(
            """SELECT opens_minute, closes_minute, last_admission_minute
               FROM travel_read.active_place_opening_rule
               WHERE fact_id = 'travel.place-opening-rule:live-rule'"""
        ).fetchone()
    assert row == (540, 1080, 1050)


class SinglePageClient:
    """외부 네트워크 대신 공공데이터 envelope 한 페이지를 반환한다."""

    def fetch_json(self, contract, endpoint, query, environment):
        payload = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
                "body": {
                    "items": {
                        "item": [
                            {
                                "nodeid": f"JJB-{contract.id}",
                                "nodenm": "통합 검증 정류장",
                                "citycode": "39",
                                "gpslati": "33.500000",
                                "gpslong": "126.550000",
                            }
                        ]
                    },
                    "pageNo": int(query["pageNo"]),
                    "numOfRows": int(query["numOfRows"]),
                    "totalCount": 1,
                },
            }
        }
        raw = json.dumps(payload, ensure_ascii=False).encode()
        return FetchedJson(raw, payload, datetime.now(UTC))


@pytest.mark.skipif(
    not all(os.getenv(name) for name in REQUIRED),
    reason="로컬 PostGIS와 MinIO 접속정보가 모두 제공된 경우에만 실행합니다.",
)
def test_live_refresh_persists_matching_raw_and_validated_acquisition() -> None:
    """완전한 refresh는 raw와 acquisition을 일치시킨 뒤 VALIDATED가 되어야 한다."""

    boto_client = boto3.client(
        "s3",
        endpoint_url=os.environ["JEJU_TEST_S3_ENDPOINT"],
        aws_access_key_id=os.environ["JEJU_TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["JEJU_TEST_S3_SECRET_KEY"],
        region_name="ap-northeast-2",
    )
    base = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    contract = base
    repository = PostgresSourceAdminRepository(
        os.environ["JEJU_TEST_ADMIN_DSN"], assume_role="jeju_importer"
    )
    service = PublicDataRefreshService(
        SinglePageClient(),
        PrivateRawObjectStore(S3ObjectClient(boto_client)),
        repository,
    )

    publisher = PostgresBusStopPublisher(
        os.environ["JEJU_TEST_ADMIN_DSN"], assume_role="jeju_importer"
    )
    outcome = service.refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "integration-only"},
        normalize_tago_stops,
        publish_validated=publisher.publish,
    )

    assert outcome.status == "STAGED"
    assert outcome.publication_id is not None
    PostgresProjectionPublisher(
        os.environ["JEJU_TEST_ADMIN_DSN"], assume_role="jeju_importer"
    ).publish_coverage(
        UUID(outcome.publication_id),
        (SourceCoverageRecord("bus_stop_catalog_ready", 1.0),),
    )
    with psycopg.connect(os.environ["JEJU_TEST_ADMIN_DSN"]) as connection:
        acquisition = connection.execute(
            """SELECT acquisition_id FROM source_admin.acquisition
               WHERE source_id = %s""",
            (contract.id,),
        ).fetchone()
    assert acquisition is not None
    records = normalize_tago_stops(
        [
            {
                "nodeid": f"JJB-{contract.id}",
                "nodenm": "통합 검증 정류장",
                "citycode": "39",
                "gpslati": "33.500000",
                "gpslong": "126.550000",
            }
        ]
    ).records
    repeated = publisher.publish(acquisition[0], records)

    with psycopg.connect(os.environ["JEJU_TEST_ADMIN_DSN"]) as connection:
        row = connection.execute(
            """SELECT a.status, a.raw_checksum, r.checksum, r.object_version_id,
                      a.accepted_row_count, a.rejected_row_count
               FROM source_admin.acquisition AS a
               JOIN source_admin.raw_object AS r
                 ON r.source_id = a.source_id AND r.checksum = a.raw_checksum
               WHERE a.source_id = %s""",
            (contract.id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "PUBLISHED"
    assert row[1] == row[2]
    assert row[3]
    assert row[4:] == (1, 0)
    assert outcome.publication_id is not None
    assert outcome.dataset_version is not None
    assert repeated.created is False
    assert str(repeated.publication_id) == outcome.publication_id
    with psycopg.connect(os.environ["JEJU_TEST_ADMIN_DSN"]) as connection:
        connection.execute("SET ROLE jeju_runtime")
        active = connection.execute(
            """SELECT provider_stop_id, publication_id
               FROM travel_read.active_bus_stop WHERE source_id = %s""",
            (contract.id,),
        ).fetchone()
    assert active == (f"JJB-{contract.id}", repeated.publication_id)


@pytest.mark.skipif(
    not all(os.getenv(name) for name in REQUIRED),
    reason="로컬 PostGIS와 MinIO 접속정보가 모두 제공된 경우에만 실행합니다.",
)
def test_live_projection_failure_rolls_back_publication_and_activation() -> None:
    """projection INSERT가 실패하면 publication과 active snapshot을 모두 rollback해야 한다."""

    boto_client = boto3.client(
        "s3",
        endpoint_url=os.environ["JEJU_TEST_S3_ENDPOINT"],
        aws_access_key_id=os.environ["JEJU_TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["JEJU_TEST_S3_SECRET_KEY"],
        region_name="ap-northeast-2",
    )
    base = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    contract = base.model_copy(update={"id": f"integration.rollback-{uuid4().hex}"})
    repository = PostgresSourceAdminRepository(
        os.environ["JEJU_TEST_ADMIN_DSN"], assume_role="jeju_importer"
    )
    outcome = PublicDataRefreshService(
        SinglePageClient(),
        PrivateRawObjectStore(S3ObjectClient(boto_client)),
        repository,
    ).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "integration-only"},
        normalize_tago_stops,
    )
    assert outcome.status == "VALIDATED"

    with psycopg.connect(os.environ["JEJU_TEST_ADMIN_DSN"]) as connection:
        acquisition = connection.execute(
            """SELECT acquisition_id FROM source_admin.acquisition
               WHERE source_id = %s""",
            (contract.id,),
        ).fetchone()
    assert acquisition is not None
    records = normalize_tago_stops(
        [
            {
                "nodeid": f"JJB-{contract.id}",
                "nodenm": "rollback 검증 정류장",
                "citycode": "39",
                "gpslati": "33.500000",
                "gpslong": "126.550000",
            }
        ]
    ).records
    publisher = PostgresBusStopPublisher(
        os.environ["JEJU_TEST_ADMIN_DSN"], assume_role="jeju_importer"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        publisher.publish(acquisition[0], (*records, *records))

    with psycopg.connect(os.environ["JEJU_TEST_ADMIN_DSN"]) as connection:
        state = connection.execute(
            """SELECT a.status,
                      (SELECT count(*) FROM source_admin.publication p
                       WHERE p.acquisition_id = a.acquisition_id),
                      (SELECT count(*) FROM source_admin.active_snapshot s
                       WHERE s.source_id = a.source_id)
               FROM source_admin.acquisition a WHERE a.acquisition_id = %s""",
            (acquisition[0],),
        ).fetchone()
    assert state == ("VALIDATED", 0, 0)
