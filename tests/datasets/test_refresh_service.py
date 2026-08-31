"""공공데이터 raw-first refresh orchestration 테스트."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from jeju_trip.application.refresh_service import PublicDataRefreshService
from jeju_trip.infrastructure.normalization_spool import Rejection
from jeju_trip.infrastructure.public_data_http import FetchedJson, PublicDataHttpError
from jeju_trip.infrastructure.public_data_normalizers import (
    HolidayRecord,
    NormalizedBatch,
    normalize_tago_stops,
)
from jeju_trip.infrastructure.raw_page_spool import RawPageSpool
from jeju_trip.infrastructure.raw_store import RawObjectPointer
from jeju_trip.infrastructure.source_admin_repository import AcquisitionRegistration
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from tests.datasets.test_public_data_http import _response_payload

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class FakePublication:
    publication_id: object
    dataset_version: str
    created: bool = True


class FakePageClient:
    def __init__(self, interrupt_on_page: int | None = None, outside_jeju: bool = False) -> None:
        self.interrupt_on_page = interrupt_on_page
        self.outside_jeju = outside_jeju

    def fetch_json(self, contract, endpoint, query, environment):
        page = int(query["pageNo"])
        if page == self.interrupt_on_page:
            raise TimeoutError
        raw = _response_payload(
            page,
            2,
            [
                {
                    "nodeid": f"JJB-{page}",
                    "nodenm": f"제주 정류장 {page}",
                    "citycode": "39",
                    "gpslati": "37.56" if self.outside_jeju else "33.50",
                    "gpslong": "126.98" if self.outside_jeju else f"126.{50 + page}",
                }
            ],
        )
        return FetchedJson(raw, {}, datetime.now(UTC))


class InvalidEnvelopePageClient:
    def fetch_json(self, contract, endpoint, query, environment):
        return FetchedJson(b'{"malformed":true}', {}, datetime.now(UTC))


class FlakyEnvelopePageClient:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_json(self, contract, endpoint, query, environment):
        self.calls += 1
        raw = (
            b'{"malformed":true}'
            if self.calls == 1
            else _response_payload(
                1,
                1,
                [
                    {
                        "nodeid": "JJB-1",
                        "nodenm": "제주 정류장",
                        "citycode": "39",
                        "gpslati": "33.50",
                        "gpslong": "126.50",
                    }
                ],
            )
        )
        return FetchedJson(raw, {}, datetime.now(UTC))


class TrackingSinglePageClient:
    def __init__(self) -> None:
        self.content_ids: list[str] = []

    def fetch_json(self, contract, endpoint, query, environment):
        self.content_ids.append(str(query["contentId"]))
        raw = _response_payload(
            1,
            1,
            [
                {
                    "contentid": str(query["contentId"]),
                    "contenttypeid": str(query["contentTypeId"]),
                    "usetime": "매일 09:00~18:00",
                }
            ],
        )
        return FetchedJson(raw, {}, datetime.now(UTC))


class FakeRawStore:
    def __init__(self) -> None:
        self.archives: list[bytes] = []

    def store_verified(self, contract, chunks, extension, content_type, register_acquisition):
        body = b"".join(chunks)
        self.archives.append(body)
        checksum = hashlib.sha256(body).hexdigest()
        pointer = RawObjectPointer(
            source_id=contract.id,
            checksum=checksum,
            bucket="jeju-private-raw",
            object_key=f"raw/v1/{contract.id}/{checksum[:2]}/{checksum}.zip",
            object_version_id="v1",
            byte_length=len(body),
            content_type=content_type,
        )
        register_acquisition(pointer)
        return pointer


class FakeSourceAdmin:
    def __init__(self, previous_row_count: int | None = None) -> None:
        self.initial_statuses: list[str] = []
        self.validated: list[tuple[object, ...]] = []
        self.quality_failed: list[tuple[object, ...]] = []
        self.previous_row_count = previous_row_count

    def previous_published_row_count(self, source_id):
        return self.previous_row_count

    def register_verified_raw(self, contract, pointer, **arguments):
        self.initial_statuses.append(arguments["initial_status"])
        return AcquisitionRegistration(uuid4(), True, arguments["initial_status"])

    def mark_validated(self, acquisition_id, accepted, rejected, checksum):
        self.validated.append((acquisition_id, accepted, rejected, checksum))

    def mark_quality_failed(self, acquisition_id, accepted, rejected):
        self.quality_failed.append((acquisition_id, accepted, rejected))


def test_complete_refresh_reaches_validated_after_raw_registration() -> None:
    """완성된 pagination은 raw HEAD 검증과 acquisition 뒤에만 VALIDATED로 전환해야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    raw_store = FakeRawStore()
    source_admin = FakeSourceAdmin()
    service = PublicDataRefreshService(FakePageClient(), raw_store, source_admin)
    outcome = service.refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
    )
    assert outcome.status == "VALIDATED"
    assert source_admin.initial_statuses == ["ACQUIRED"]
    assert len(source_admin.validated) == 1
    assert raw_store.archives[0].startswith(b"PK")


def test_invalid_envelope_is_archived_before_the_refresh_fails() -> None:
    """파싱할 수 없는 API 응답도 먼저 private raw에 보관한 뒤 INCOMPLETE로 끝나야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    raw_store = FakeRawStore()

    outcome = PublicDataRefreshService(
        InvalidEnvelopePageClient(), raw_store, FakeSourceAdmin()
    ).refresh(
        contract,
        "getSttnNoList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
    )

    assert outcome.status == "INCOMPLETE"
    assert outcome.reason_code == "PUBLIC_DATA_ENVELOPE_INVALID"
    assert len(raw_store.archives) == 1
    with zipfile.ZipFile(BytesIO(raw_store.archives[0])) as archive:
        assert archive.read("query-000001-page-000001.json") == b'{"malformed":true}'


def test_invalid_envelope_retries_without_discarding_the_first_raw_response() -> None:
    """일시적 envelope 오류는 각 원문 시도를 보존하면서 제한 횟수 안에서 재시도해야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    raw_store = FakeRawStore()
    client = FlakyEnvelopePageClient()

    outcome = PublicDataRefreshService(client, raw_store, FakeSourceAdmin()).refresh(
        contract,
        "getSttnNoList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
    )

    assert outcome.status == "VALIDATED"
    assert client.calls == 2
    with zipfile.ZipFile(BytesIO(raw_store.archives[0])) as archive:
        assert archive.read("query-000001-page-000001.json") == b'{"malformed":true}'
        assert "query-000001-page-000001-attempt-000002.json" in archive.namelist()


def test_batch_refresh_archives_complete_query_scope_without_secret() -> None:
    """부분 조회 묶음은 모든 조회값과 개수를 남기되 API 비밀값은 보존하지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    raw_store = FakeRawStore()
    outcome = PublicDataRefreshService(
        FakePageClient(), raw_store, FakeSourceAdmin()
    ).refresh_batch(
        contract,
        "getCrdntPrxmtSttnList",
        ({"cityCode": "39"}, {"cityCode": "39010", "serviceKey": "do-not-store"}),
        {"JEJU_TAGO_SERVICE_KEY": "also-do-not-store"},
        normalize_tago_stops,
    )

    with zipfile.ZipFile(BytesIO(raw_store.archives[0])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = archive.namelist()

    assert outcome.status == "VALIDATED"
    assert manifest["query_count"] == 2
    assert manifest["queries"] == [{"cityCode": "39"}, {"cityCode": "39010"}]
    assert len([name for name in names if name.endswith(".json") and name != "manifest.json"]) == 4


def test_interrupted_refresh_registers_incomplete_and_never_validates() -> None:
    """중단된 pagination은 raw를 INCOMPLETE로 보존하고 정규화·검증을 시작하지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    raw_store = FakeRawStore()
    source_admin = FakeSourceAdmin()
    service = PublicDataRefreshService(FakePageClient(interrupt_on_page=2), raw_store, source_admin)
    outcome = service.refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
    )
    assert outcome.status == "INCOMPLETE"
    assert source_admin.initial_statuses == ["INCOMPLETE"]
    assert source_admin.validated == []


def test_staging_supplement_is_archived_but_never_validated() -> None:
    """호출량 절약용 단건 보충 조회는 성공 응답이어도 완전 snapshot으로 검증하지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    raw_store = FakeRawStore()
    source_admin = FakeSourceAdmin()
    outcome = PublicDataRefreshService(FakePageClient(), raw_store, source_admin).refresh_batch(
        contract,
        "getCrdntPrxmtSttnList",
        ({"cityCode": "39"},),
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
        incomplete_reason_code="STAGING_SUPPLEMENTAL_QUERY",
    )

    assert outcome.status == "INCOMPLETE"
    assert outcome.reason_code == "STAGING_SUPPLEMENTAL_QUERY"
    assert source_admin.initial_statuses == ["INCOMPLETE"]
    assert source_admin.validated == []


def test_incomplete_aggregate_refresh_stops_after_first_failed_query() -> None:
    """대량 집계 수집은 첫 실패 뒤 남은 호출을 낭비하지 않고 재개 가능한 raw로 닫아야 한다."""

    class FirstQueryFails:
        def __init__(self) -> None:
            self.content_ids: list[str] = []

        def fetch_json(self, contract, endpoint, query, environment):
            self.content_ids.append(str(query["contentId"]))
            raise httpx.TimeoutException("timeout")

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tourapi.place-intro")
    client = FirstQueryFails()
    raw_store = FakeRawStore()
    outcome = PublicDataRefreshService(client, raw_store, FakeSourceAdmin()).refresh_batch(
        contract,
        "detailIntro2",
        (
            {"contentId": "1", "contentTypeId": "12"},
            {"contentId": "2", "contentTypeId": "12"},
        ),
        {"JEJU_TOURAPI_SERVICE_KEY": "test-key"},
        lambda rows: NormalizedBatch((), ()),
        stop_on_incomplete=True,
    )

    assert outcome.status == "INCOMPLETE"
    assert outcome.query_count == 2
    assert outcome.completed_query_count == 0
    assert client.content_ids == ["1", "1", "1"]


def test_rate_limited_aggregate_refresh_stops_without_immediate_retries() -> None:
    """호출 제한 응답은 연속 재시도하지 않고 한 번에 재개 가능한 상태로 닫아야 한다."""

    class RateLimitedClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_json(self, contract, endpoint, query, environment):
            self.calls += 1
            raise PublicDataHttpError("HTTP_STATUS_429")

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require(
        "tourapi.place-intro"
    )
    client = RateLimitedClient()
    outcome = PublicDataRefreshService(client, FakeRawStore(), FakeSourceAdmin()).refresh_batch(
        contract,
        "detailIntro2",
        ({"contentId": "1", "contentTypeId": "12"},),
        {"JEJU_TOURAPI_SERVICE_KEY": "test-key"},
        lambda rows: NormalizedBatch((), ()),
        stop_on_incomplete=True,
    )

    assert outcome.status == "INCOMPLETE"
    assert outcome.reason_code == "HTTP_STATUS_429"
    assert outcome.completed_query_count == 0
    assert client.calls == 1


def test_incomplete_aggregate_raw_resumes_only_missing_queries() -> None:
    """
    같은 조회 manifest의 신선한 incomplete raw는
    완료 페이지를 재사용하고 누락 조회만 호출해야 한다.
    """

    queries = (
        {"contentId": "1", "contentTypeId": "12"},
        {"contentId": "2", "contentTypeId": "12"},
    )
    first_raw = _response_payload(
        1,
        1,
        [{"contentid": "1", "contenttypeid": "12", "usetime": "매일 09:00~18:00"}],
    )
    with RawPageSpool() as spool:
        spool.add_manifest(
            {
                "schema_version": "1",
                "source_id": "tourapi.place-intro",
                "query_count": 2,
                "queries": list(queries),
                "scope_expansion_from_rows": None,
            }
        )
        spool.add_scoped_raw_page(1, 1, first_raw)
        resume_archive = b"".join(spool.iter_chunks())

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tourapi.place-intro")
    client = TrackingSinglePageClient()
    raw_store = FakeRawStore()
    source_admin = FakeSourceAdmin()
    outcome = PublicDataRefreshService(client, raw_store, source_admin).refresh_batch(
        contract,
        "detailIntro2",
        queries,
        {"JEJU_TOURAPI_SERVICE_KEY": "test-key"},
        lambda rows: NormalizedBatch(
            tuple(
                HolidayRecord(
                    fact_id=f"tour-intro-test:{index}",
                    holiday_date=date(2026, 8, 24),
                    name="운영시간 테스트",
                    is_public_institution_holiday=False,
                    date_kind="00",
                )
                for index, _ in enumerate(rows)
            ),
            (),
        ),
        resume_raw_archive=resume_archive,
        resume_observed_at=datetime.now(UTC) - timedelta(hours=1),
        stop_on_incomplete=True,
    )

    assert outcome.status == "VALIDATED"
    assert outcome.query_count == 2
    assert outcome.completed_query_count == 2
    assert client.content_ids == ["2"]
    with zipfile.ZipFile(BytesIO(raw_store.archives[0])) as archive:
        assert "query-000001-page-000001.json" in archive.namelist()
        assert "query-000002-page-000001.json" in archive.namelist()


def test_stale_incomplete_raw_cannot_be_resumed_as_a_fresh_snapshot() -> None:
    """계약 신선도를 넘긴 incomplete raw는 새 관측으로 둔갑시켜 재개할 수 없어야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tourapi.place-intro")
    with pytest.raises(ValueError, match="REFRESH_RESUME_SNAPSHOT_STALE"):
        PublicDataRefreshService(
            TrackingSinglePageClient(), FakeRawStore(), FakeSourceAdmin()
        ).refresh_batch(
            contract,
            "detailIntro2",
            ({"contentId": "1", "contentTypeId": "12"},),
            {"JEJU_TOURAPI_SERVICE_KEY": "test-key"},
            lambda rows: NormalizedBatch((), ()),
            resume_raw_archive=b"unused",
            resume_observed_at=datetime.now(UTC)
            - timedelta(days=contract.temporal.freshness_days + 1),
            stop_on_incomplete=True,
        )


def test_refresh_quality_failure_keeps_previous_snapshot_unpublished() -> None:
    """좌표 품질 기준을 벗어난 refresh는 QUALITY_FAILED까지만 기록하고 검증하지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    source_admin = FakeSourceAdmin()
    service = PublicDataRefreshService(
        FakePageClient(outside_jeju=True), FakeRawStore(), source_admin
    )
    outcome = service.refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
    )
    assert outcome.status == "QUALITY_FAILED"
    assert source_admin.validated == []
    assert len(source_admin.quality_failed) == 1


def test_coordinate_ratio_counts_records_with_actual_positions() -> None:
    """좌표 비율은 승인 행 수가 아니라 실제 좌표를 가진 정규화 행 수로 계산해야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")

    def without_positions(rows):
        return NormalizedBatch(
            tuple(
                HolidayRecord(
                    fact_id=f"holiday:{index}",
                    holiday_date=date(2026, 8, 15),
                    name="광복절",
                    is_public_institution_holiday=True,
                    date_kind="01",
                )
                for index, _ in enumerate(rows)
            ),
            (),
        )

    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), FakeSourceAdmin()).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        without_positions,
    )

    assert outcome.status == "QUALITY_FAILED"


def test_refresh_rejects_empty_normalized_publication() -> None:
    """원본이 완전해도 검증된 정규화 행이 0건이면 빈 publication을 만들지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tourapi.place-intro")
    source_admin = FakeSourceAdmin()

    def reject_all(rows):
        return NormalizedBatch(
            (),
            tuple(Rejection(None, "opening_hours", "OPENING_HOURS_UNVERIFIED") for _ in rows),
        )

    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), source_admin).refresh(
        contract,
        "detailIntro2",
        {},
        {"JEJU_TOURAPI_SERVICE_KEY": "test-key"},
        reject_all,
    )

    assert outcome.status == "QUALITY_FAILED"
    assert outcome.reason_code == "NORMALIZED_RECORDS_EMPTY"
    assert source_admin.validated == []
    assert len(source_admin.quality_failed) == 1


def test_complete_refresh_can_publish_validated_records_in_same_flow() -> None:
    """완성된 refresh는 검증 record만 publisher에 전달하고 PUBLISHED를 반환해야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    source_admin = FakeSourceAdmin()
    published: list[tuple[object, tuple[object, ...]]] = []

    def publish(acquisition_id, records):
        published.append((acquisition_id, records))
        return FakePublication(uuid4(), "2026-07-21-aaaaaaaaaaaa")

    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), source_admin).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
        publish_validated=publish,
    )

    assert outcome.status == "STAGED"
    assert outcome.publication_id is not None
    assert outcome.dataset_version == "2026-07-21-aaaaaaaaaaaa"
    assert len(published) == 1
    assert len(published[0][1]) == 2


def test_normalized_duplicate_publication_returns_no_change() -> None:
    """새 raw가 기존 active dataset과 동일하게 정규화되면 STAGED가 아닌 NO_CHANGE여야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), FakeSourceAdmin()).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
        publish_validated=lambda acquisition_id, records: FakePublication(
            uuid4(), "2026-07-21-aaaaaaaaaaaa", created=False
        ),
    )

    assert outcome.status == "NO_CHANGE"
    assert outcome.publication_id is not None
    assert outcome.dataset_version == "2026-07-21-aaaaaaaaaaaa"


def test_existing_validated_acquisition_resumes_publication_without_revalidation() -> None:
    """
    publication 실패 뒤 VALIDATED acquisition은
    검증 상태를 되돌리지 않고 발행을 재개해야 한다.
    """

    acquisition_id = uuid4()

    class ExistingValidatedSourceAdmin(FakeSourceAdmin):
        def register_verified_raw(self, contract, pointer, **arguments):
            self.initial_statuses.append(arguments["initial_status"])
            return AcquisitionRegistration(acquisition_id, False, "VALIDATED")

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    source_admin = ExistingValidatedSourceAdmin()
    published: list[tuple[object, tuple[object, ...]]] = []

    def publish(existing_acquisition_id, records):
        published.append((existing_acquisition_id, records))
        return FakePublication(uuid4(), "2026-07-21-aaaaaaaaaaaa", created=False)

    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), source_admin).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
        publish_validated=publish,
    )

    assert outcome.status == "NO_CHANGE"
    assert published[0][0] == acquisition_id
    assert source_admin.validated == []


def test_abrupt_row_count_change_fails_before_publication() -> None:
    """이전 publication 대비 행 수 변화가 계약 한도를 넘으면 active snapshot을 유지해야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    source_admin = FakeSourceAdmin(previous_row_count=10)
    published: list[object] = []
    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), source_admin).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
        publish_validated=lambda publication, rows: published.append((publication, rows)),
    )
    assert outcome.status == "QUALITY_FAILED"
    assert outcome.reason_code == "SOURCE_ROW_CHANGE_RATIO_EXCEEDED"
    assert published == []


def test_reviewed_scope_expansion_allows_only_a_matching_active_baseline() -> None:
    """검토된 전역 확대는 지정한 활성 행 수가 정확히 일치할 때만 증가 급변을 허용해야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    source_admin = FakeSourceAdmin(previous_row_count=1)

    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), source_admin).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
        scope_expansion_from_rows=1,
    )

    assert outcome.status == "VALIDATED"


def test_reviewed_scope_expansion_rejects_a_stale_active_baseline() -> None:
    """전역 확대 기준 행 수가 현재 publication과 다르면 급변 예외를 적용하지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    source_admin = FakeSourceAdmin(previous_row_count=1)

    outcome = PublicDataRefreshService(FakePageClient(), FakeRawStore(), source_admin).refresh(
        contract,
        "getCrdntPrxmtSttnList",
        {},
        {"JEJU_TAGO_SERVICE_KEY": "test-key"},
        normalize_tago_stops,
        scope_expansion_from_rows=2,
    )

    assert outcome.status == "QUALITY_FAILED"
    assert outcome.reason_code == "SOURCE_SCOPE_EXPANSION_BASELINE_MISMATCH"
