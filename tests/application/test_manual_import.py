"""수동 CSV raw-first import 서비스 테스트."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from jeju_trip.application.manual_import import ManualCsvImportService, validate_timetable_bundle
from jeju_trip.infrastructure.projection_publisher import ScopeManifestBundle, TimetableBundle
from jeju_trip.infrastructure.public_data_normalizers import (
    normalize_bus_route_stops,
    normalize_jeju_boundary,
    normalize_place_entrances,
    normalize_scheduled_stop_times,
    normalize_scheduled_trips,
    normalize_service_calendars,
)
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from tests.datasets.test_refresh_service import (
    FakePublication,
    FakeRawStore,
    FakeSourceAdmin,
)

ROOT = Path(__file__).resolve().parents[2]


def _entrance_csv(path: Path) -> None:
    path.write_text(
        "place_fact_id,entrance_id,entrance_type,latitude,longitude,"
        "verification_method,verified_at,expires_at,supported_modes,source_reference\n"
        "tourapi.place:1,gate-1,main,33.5,126.5,CURATED,"
        "2026-08-10T10:00:00+09:00,2027-08-10T10:00:00+09:00,walk,"
        "internal://review/1\n",
        encoding="utf-8",
    )


def test_manual_csv_is_archived_validated_and_published(tmp_path: Path) -> None:
    """수동 입구 CSV도 원본 검증·정규화 checksum 뒤에만 publication해야 한다."""

    path = tmp_path / "entrances.csv"
    _entrance_csv(path)
    raw_store = FakeRawStore()
    source_admin = FakeSourceAdmin()
    service = ManualCsvImportService(raw_store, source_admin)
    published: list[tuple[object, tuple[object, ...]]] = []
    outcome = service.import_single(
        SourceCatalog.load(ROOT / "config/data_sources.toml").require("travel.place-entrance-map"),
        path,
        date(2026, 8, 10),
        normalize_place_entrances,
        lambda acquisition, records: (
            published.append((acquisition, records)) or FakePublication(uuid4(), "2026-08-10-test")
        ),
    )
    assert outcome.status == "STAGED"
    assert raw_store.archives[0].startswith(b"PK")
    assert len(source_admin.validated) == 1
    assert len(published[0][1]) == 1


def test_official_timetable_zip_is_stored_before_bundle_normalization(
    tmp_path: Path, monkeypatch
) -> None:
    """공식 시간표 ZIP은 private raw 등록 뒤에만 typed bundle로 정규화해 publication해야 한다."""

    path = tmp_path / "official-timetable.zip"
    path.write_bytes(b"PK\x03\x04official-timetable")
    events: list[str] = []
    route_stops = normalize_bus_route_stops(
        [
            {
                "route_fact_id": "tago.bus-route:1",
                "stop_fact_id": "tago.bus-stop:1",
                "route_sequence": "1",
                "direction_text": "성산 방면",
                "latitude": "33.45",
                "longitude": "126.9",
            },
            {
                "route_fact_id": "tago.bus-route:1",
                "stop_fact_id": "tago.bus-stop:2",
                "route_sequence": "2",
                "direction_text": "성산 방면",
                "latitude": "33.46",
                "longitude": "126.91",
            },
        ]
    ).records
    calendars = normalize_service_calendars(
        [
            {
                "service_id": "weekday-2026-08-14",
                "day_type": "WEEKDAY",
                "starts_on": "2026-08-14",
                "ends_on": "2026-08-14",
            }
        ]
    ).records
    trips = normalize_scheduled_trips(
        [
            {
                "trip_id": "trip-1",
                "route_fact_id": "tago.bus-route:1",
                "service_id": "weekday-2026-08-14",
                "direction_text": "성산 방면",
                "timetable_effective_from": "2026-01-01",
                "timetable_effective_to": "2026-12-31",
            }
        ]
    ).records
    stop_times = normalize_scheduled_stop_times(
        [
            {
                "trip_id": "trip-1",
                "stop_id": "tago.bus-stop:1",
                "stop_sequence": "1",
                "arrival_time": "0900",
                "departure_time": "0900",
            },
            {
                "trip_id": "trip-1",
                "stop_id": "tago.bus-stop:2",
                "stop_sequence": "2",
                "arrival_time": "0930",
                "departure_time": "0930",
            },
        ]
    ).records
    bundle = TimetableBundle(route_stops, calendars, trips, stop_times)

    monkeypatch.setattr(
        "jeju_trip.application.manual_import.load_official_timetable_zip",
        lambda stream: events.append("normalize") or object(),
    )
    monkeypatch.setattr(
        "jeju_trip.application.manual_import.build_weekday_timetable_bundle",
        lambda raw, target_date: bundle,
    )

    class OrderedRawStore(FakeRawStore):
        def store_verified(self, *args, **kwargs):
            events.append("store")
            return super().store_verified(*args, **kwargs)

    published: list[TimetableBundle] = []
    service = ManualCsvImportService(OrderedRawStore(), FakeSourceAdmin())
    outcome = service.import_official_timetable(
        SourceCatalog.load(ROOT / "config/data_sources.toml").require("jeju.bus-timetable"),
        path,
        date(2026, 8, 14),
        lambda acquisition, value: (
            published.append(value) or FakePublication(uuid4(), "2026-08-14-weekday")
        ),
    )

    assert outcome.status == "STAGED"
    assert events == ["store", "normalize"]
    assert published == [bundle]


def test_manual_csv_abrupt_row_change_does_not_publish(tmp_path: Path) -> None:
    """수동 파일도 이전 active 행 수 대비 급변하면 새 snapshot을 활성화하지 않아야 한다."""

    path = tmp_path / "entrances.csv"
    _entrance_csv(path)
    source_admin = FakeSourceAdmin(previous_row_count=10)
    published: list[object] = []
    outcome = ManualCsvImportService(FakeRawStore(), source_admin).import_single(
        SourceCatalog.load(ROOT / "config/data_sources.toml").require("travel.place-entrance-map"),
        path,
        date(2026, 8, 10),
        normalize_place_entrances,
        lambda acquisition, records: published.append((acquisition, records)),
    )
    assert outcome.status == "QUALITY_FAILED"
    assert outcome.reason_code == "SOURCE_ROW_CHANGE_RATIO_EXCEEDED"
    assert published == []


def test_reviewed_manual_scope_expansion_requires_matching_active_baseline(
    tmp_path: Path,
) -> None:
    """수동 전역 확대는 명시한 이전 active 행 수가 실제 기준과 같을 때만 발행해야 한다."""

    path = tmp_path / "entrances.csv"
    path.write_text(
        "place_fact_id,entrance_id,entrance_type,latitude,longitude,"
        "verification_method,verified_at,expires_at,supported_modes,source_reference\n"
        "tourapi.place:1,gate-1,main,33.5,126.5,CURATED,"
        "2026-08-10T10:00:00+09:00,2027-08-10T10:00:00+09:00,walk,"
        "internal://review/1\n"
        "tourapi.place:2,gate-2,main,33.6,126.6,CURATED,"
        "2026-08-10T10:00:00+09:00,2027-08-10T10:00:00+09:00,walk,"
        "internal://review/2\n",
        encoding="utf-8",
    )
    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require(
        "travel.place-entrance-map"
    )
    published: list[object] = []
    accepted = ManualCsvImportService(
        FakeRawStore(), FakeSourceAdmin(previous_row_count=1)
    ).import_single(
        source,
        path,
        date(2026, 8, 10),
        normalize_place_entrances,
        lambda acquisition, records: (
            published.append((acquisition, records))
            or FakePublication(uuid4(), "2026-08-10-expanded")
        ),
        scope_expansion_from_rows=1,
    )
    rejected = ManualCsvImportService(
        FakeRawStore(), FakeSourceAdmin(previous_row_count=1)
    ).import_single(
        source,
        path,
        date(2026, 8, 10),
        normalize_place_entrances,
        lambda acquisition, records: FakePublication(uuid4(), "unused"),
        scope_expansion_from_rows=2,
    )

    assert accepted.status == "STAGED"
    assert len(published) == 1
    assert rejected.status == "QUALITY_FAILED"
    assert rejected.reason_code == "SOURCE_SCOPE_EXPANSION_BASELINE_MISMATCH"


def test_scope_manifest_archives_three_csv_files_before_publication(tmp_path: Path) -> None:
    """동부 scope 구성원과 전략 template은 하나의 raw 묶음으로 검증한 뒤 발행해야 한다."""

    paths = {
        "service-scope-members.csv": tmp_path / "service-scope-members.csv",
        "itinerary-template-steps.csv": tmp_path / "itinerary-template-steps.csv",
        "itinerary-template-candidates.csv": tmp_path / "itinerary-template-candidates.csv",
    }
    paths["service-scope-members.csv"].write_text(
        "fact_id,region_code,grid_id,member_type,member_id,role,required,source_reference\n"
        "scope-place-1,JEJU_EAST,POC_V1,PLACE,tourapi.place:1,required_visit,true,"
        "https://example.go.kr/scope/1\n",
        encoding="utf-8",
    )
    paths["itinerary-template-steps.csv"].write_text(
        "fact_id,region_code,grid_id,strategy,sequence,activity_type,candidate_group,required,"
        "source_reference\n"
        "step-1,JEJU_EAST,POC_V1,balanced,1,visit,east-required,true,"
        "https://example.go.kr/scope/1\n",
        encoding="utf-8",
    )
    paths["itinerary-template-candidates.csv"].write_text(
        "fact_id,region_code,grid_id,candidate_group,place_fact_id,priority,source_reference\n"
        "candidate-1,JEJU_EAST,POC_V1,east-required,tourapi.place:1,1,"
        "https://example.go.kr/scope/1\n",
        encoding="utf-8",
    )
    published: list[tuple[object, ScopeManifestBundle]] = []

    outcome = ManualCsvImportService(FakeRawStore(), FakeSourceAdmin()).import_scope_manifest(
        SourceCatalog.load(ROOT / "config/data_sources.toml").require(
            "travel.service-scope-manifest"
        ),
        paths,
        date(2026, 8, 11),
        lambda acquisition, bundle: (
            published.append((acquisition, bundle)) or FakePublication(uuid4(), "2026-08-11-scope")
        ),
    )

    assert outcome.status == "STAGED"
    assert len(published) == 1
    assert len(published[0][1].members) == 1
    assert len(published[0][1].steps) == 1
    assert len(published[0][1].candidates) == 1


def test_boundary_import_preserves_official_zip_before_single_record_publication(
    tmp_path: Path, monkeypatch
) -> None:
    """경계 importer는 ZIP을 재포장하지 않고 그대로 보존한 뒤 제주 record 하나만 발행해야 한다."""

    raw = b"PK\x03\x04official-sgis-archive"
    path = tmp_path / "boundary.zip"
    path.write_bytes(raw)
    normalized = normalize_jeju_boundary(
        {
            "boundary_id": "jeju-all",
            "name": "제주특별자치도",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[126.0, 33.0], [127.0, 33.0], [127.0, 33.7], [126.0, 33.0]]]],
            },
            "source_reference": "https://www.data.go.kr/data/15129688/fileData.do",
        }
    )
    events: list[str] = []

    def normalize_after_store(raw, source_date):
        events.append("normalize")
        return normalized

    monkeypatch.setattr(
        "jeju_trip.application.manual_import.normalize_sgis_jeju_boundary_zip",
        normalize_after_store,
    )
    monkeypatch.setattr(
        "jeju_trip.application.manual_import.assert_official_sgis_boundary_checksum",
        lambda raw: None,
    )

    class OrderedRawStore(FakeRawStore):
        def store_verified(self, *args, **kwargs):
            events.append("store")
            return super().store_verified(*args, **kwargs)

    raw_store = OrderedRawStore()
    published: list[tuple[object, tuple[object, ...]]] = []
    outcome = ManualCsvImportService(raw_store, FakeSourceAdmin()).import_boundary_zip(
        SourceCatalog.load(ROOT / "config/data_sources.toml").require("spatial.jeju-boundary"),
        path,
        date(2025, 6, 30),
        lambda acquisition, records: (
            published.append((acquisition, records)) or FakePublication(uuid4(), "2025-06-30-test")
        ),
    )

    assert outcome.status == "STAGED"
    assert events == ["store", "normalize"]
    assert raw_store.archives == [raw]
    assert len(published[0][1]) == 1


def test_timetable_bundle_rejects_unknown_trip_stop_time() -> None:
    """시간표 stop time이 존재하지 않는 trip을 참조하면 publication 전에 거부해야 한다."""

    route_stops = normalize_bus_route_stops(
        [
            {
                "route_fact_id": "route-1",
                "stop_fact_id": "stop-1",
                "route_sequence": "1",
                "direction_text": "동쪽",
                "latitude": "33.459",
                "longitude": "126.936",
            },
            {
                "route_fact_id": "route-1",
                "stop_fact_id": "stop-2",
                "route_sequence": "2",
                "direction_text": "동쪽",
                "latitude": "33.460",
                "longitude": "126.937",
            },
        ]
    ).records
    calendars = normalize_service_calendars(
        [
            {
                "service_id": "weekday",
                "day_type": "WEEKDAY",
                "starts_on": "2026-01-01",
                "ends_on": "2026-12-31",
            }
        ]
    ).records
    trips = normalize_scheduled_trips(
        [
            {
                "trip_id": "trip-1",
                "route_fact_id": "route-1",
                "service_id": "weekday",
                "direction_text": "동쪽",
                "timetable_effective_from": "2026-01-01",
                "timetable_effective_to": "2026-12-31",
            }
        ]
    ).records
    stop_times = normalize_scheduled_stop_times(
        [
            {
                "trip_id": "missing",
                "stop_id": "stop-1",
                "stop_sequence": "1",
                "arrival_time": "0900",
                "departure_time": "0900",
            }
        ]
    ).records
    with pytest.raises(ValueError, match="TIMETABLE_STOP_TIME_TRIP_MISSING"):
        validate_timetable_bundle(TimetableBundle(route_stops, calendars, trips, stop_times))
