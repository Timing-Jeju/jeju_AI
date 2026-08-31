"""소스 승인과 secret 격리 테스트."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from jeju_trip.infrastructure.source_catalog import (
    SourceCatalog,
    SourceUrlRejectedError,
)

ROOT = Path(__file__).resolve().parents[2]


def test_official_jeju_timetable_is_approved_for_private_normalization() -> None:
    """공식 제주 시간표는 내부 정규화만 허용한 승인 수동 소스여야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require("jeju.bus-timetable")
    assert source.landing_url == "https://www.data.go.kr/data/3043887/fileData.do"
    assert source.acquisition.mode == "MANUAL"
    assert source.license.raw_private_storage_allowed is True
    assert source.license.internal_derivative_allowed is True
    assert source.license.public_redistribution_allowed is False


def test_official_timetable_accepts_only_the_approved_future_service_window() -> None:
    """공식 시간표의 미래 운행일은 계약 범위 안에서만 신선하게 판정해야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "jeju.bus-timetable"
    )
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)

    assert source.is_fresh(source_date=date(2026, 8, 31), now=now)
    assert source.is_fresh(source_date=date(2026, 10, 14), now=now)
    assert not source.is_fresh(source_date=date(2026, 10, 15), now=now)


def test_source_url_must_match_host_and_path_allowlist() -> None:
    """승인 소스도 계약의 host와 path 밖으로 요청할 수 없어야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require("tmap.pedestrian")
    with pytest.raises(SourceUrlRejectedError, match="HOST"):
        source.assert_network_request_allowed("https://example.com/transit/routes")


@pytest.mark.parametrize(
    "url",
    (
        "https://apis.data.go.kr/B551011/KorService2/../unapproved",
        "https://apis.data.go.kr/B551011/KorService2/%2e%2e/unapproved",
        "https://apis.data.go.kr/B551011/KorService2/%252e%252e/unapproved",
        "https://apis.data.go.kr:444/B551011/KorService2/areaBasedList2",
    ),
    ids=("점경로", "인코딩점경로", "이중인코딩점경로", "비표준포트"),
)
def test_source_url_rejects_normalization_and_port_bypasses(url: str) -> None:
    """승인 host 문자열과 prefix를 만족해도 정규화 뒤 경계 밖이거나 비표준 포트면 거부해야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "tourapi.place"
    )

    with pytest.raises(SourceUrlRejectedError):
        source.assert_network_request_allowed(url)


def test_source_url_accepts_normalized_approved_endpoint() -> None:
    """정규화 우회가 없는 승인 HTTPS endpoint는 기존과 같이 허용해야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "tourapi.place"
    )

    source.assert_network_request_allowed(
        "https://apis.data.go.kr/B551011/KorService2/areaBasedList2"
    )


def test_collector_receives_only_source_specific_secrets() -> None:
    """수집기에는 해당 소스에 등록된 API 키만 전달해야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require("tmap.pedestrian")
    selected = source.permitted_secrets(
        {"JEJU_TMAP_API_KEY": "tmap-secret", "JEJU_TOURAPI_SERVICE_KEY": "tour-secret"}
    )
    assert selected == {"JEJU_TMAP_API_KEY": "tmap-secret"}


def test_tmap_contract_forbids_persistent_raw_storage() -> None:
    """TMAP source contract는 원본의 private object 저장도 허용하지 않아야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require("tmap.pedestrian")
    assert source.license.raw_private_storage_allowed is False
    assert source.acquisition.retention_policy == "MEMORY_ONLY_LT_24H"


@pytest.mark.parametrize(
    "source_id",
    ["tmap.pedestrian", "tmap.driving", "tago.bus-arrival"],
    ids=["보행", "차량", "실시간도착"],
)
def test_on_demand_route_sources_are_memory_only(source_id: str) -> None:
    """경로와 실시간 도착 원본은 승인돼도 프로세스 메모리 밖에 저장하지 않아야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(source_id)
    assert source.acquisition.retention_policy == "MEMORY_ONLY_LT_24H"
    assert source.license.raw_private_storage_allowed is False


def test_official_taxi_policy_is_registered_as_versioned_manual_source() -> None:
    """제주 공식 택시 요금은 일반 코드 상수가 아닌 수동 publication 소스여야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "jeju.taxi-fare-policy"
    )
    assert source.acquisition.mode == "MANUAL"
    assert source.normalization_schema_version == "jeju-taxi-fare-v1"


def test_restaurant_dietary_map_is_a_private_curated_manual_source() -> None:
    """식이 안전성 근거는 공개 POI가 아니라 private raw-first 수동 검증 소스여야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "travel.restaurant-dietary-map"
    )

    assert source.acquisition.mode == "MANUAL"
    assert source.acquisition.retention_policy == "PRIVATE_INDEFINITE"
    assert source.normalization_schema_version == "restaurant-dietary-v1"
    assert source.license.public_redistribution_allowed is False


def test_v04_catalog_contains_only_current_planning_engine_sources() -> None:
    """1차 최종 카탈로그에는 현재 일정 판정 엔진이 실제 소비하는 소스만 있어야 한다."""

    catalog = SourceCatalog.load(ROOT / "config" / "data_sources.toml")
    source_ids = {source.id for source in catalog.sources}

    assert not source_ids & {
        "jeju-city.public-restroom",
        "public-restroom.standard",
        "visitjeju.place",
        "jeju.road-traffic",
        "kma.short-forecast",
        "hallasan.trail-control",
        "tmap.transit",
    }


def test_tourapi_intro_uses_a_separate_coordinate_free_quality_contract() -> None:
    """TourAPI 상세소개는 장소 목록과 달리 좌표 비율을 요구하지 않는 승인 계약이어야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "tourapi.place-intro"
    )
    assert source.normalization_schema_version == "tour-place-intro-hours-v12"
    assert source.quality.minimum_coordinate_ratio == 0
    assert source.acquisition.base_url.endswith("/KorService2")


def test_jeju_wide_collections_reject_trivially_small_snapshots() -> None:
    """제주 전역 장소·노선의 한두 행짜리 잘린 응답을 승인하지 않아야 한다."""

    catalog = SourceCatalog.load(ROOT / "config" / "data_sources.toml")
    assert catalog.require("tourapi.place").quality.minimum_rows >= 100
    assert catalog.require("tago.bus-route").quality.minimum_rows >= 50


def test_temporal_contract_rejects_stale_and_future_observations() -> None:
    """OBSERVED_AT 계약은 만료됐거나 허용 오차보다 미래인 관측시각을 신선하게 보지 않아야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "tourapi.place"
    )
    now = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)

    assert source.is_fresh(observed_at=now - timedelta(days=8), now=now)
    assert not source.is_fresh(observed_at=now - timedelta(days=8, seconds=1), now=now)
    assert not source.is_fresh(observed_at=now + timedelta(minutes=5, seconds=1), now=now)


def test_default_future_tolerance_preserves_unrelated_contract_fingerprint() -> None:
    """미래 허용값이 0인 기존 소스는 승인 계약 fingerprint가 불필요하게 바뀌지 않아야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "tourapi.place"
    )
    legacy_contract = source.model_dump(mode="json")
    legacy_contract["temporal"].pop("future_tolerance_days")
    expected = hashlib.sha256(
        json.dumps(legacy_contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert source.contract_fingerprint == expected


def test_official_jeju_boundary_is_approved_for_private_zip_normalization() -> None:
    """공식 SGIS 경계는 ZIP 원본 보존과 내부 정규화만 허용한 승인 소스여야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "spatial.jeju-boundary"
    )
    assert source.acquisition.format == "ZIP"
    assert source.acquisition.source_crs == "EPSG:5179"
    assert source.acquisition.maximum_response_bytes == 384 * 1024 * 1024
    assert source.license.raw_private_storage_allowed is True
    assert source.license.internal_derivative_allowed is True
    assert source.license.public_redistribution_allowed is False


def test_official_boundary_freshness_covers_announced_next_registration_date() -> None:
    """SGIS 최신 경계는 공식 차기 등록 예정일까지 stale로 오판하지 않아야 한다."""

    source = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "spatial.jeju-boundary"
    )

    assert source.is_fresh(
        source_date=date(2025, 6, 30),
        now=datetime(2027, 2, 1, 0, 0, tzinfo=UTC),
    )
    assert not source.is_fresh(
        source_date=date(2025, 6, 30),
        now=datetime(2027, 2, 2, 0, 0, tzinfo=UTC),
    )
