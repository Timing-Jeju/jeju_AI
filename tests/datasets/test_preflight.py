"""수집 전 source readiness 판정 테스트."""

from __future__ import annotations

from pathlib import Path

from jeju_trip.infrastructure.preflight import SourcePreflight
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[2]


def test_approved_api_without_secret_fails_before_network() -> None:
    """승인된 API라도 필수 secret이 없으면 네트워크 전에 실패해야 한다."""

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tourapi.place")
    result = SourcePreflight().check(source, {})
    assert result.status == "FAIL"
    assert result.reason_codes == ("SOURCE_SECRET_MISSING",)


def test_approved_api_with_contract_secret_passes_preflight() -> None:
    """승인 근거와 source 전용 secret이 모두 있으면 preflight를 통과해야 한다."""

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    result = SourcePreflight().check(source, {"JEJU_TAGO_SERVICE_KEY": "test-key"})
    assert result.status == "PASS"
    assert result.permitted_secret_names == ("JEJU_TAGO_SERVICE_KEY",)


def test_approved_source_rejects_placeholder_terms_fingerprint() -> None:
    """승인 source의 약관 fingerprint가 placeholder이면 preflight를 거부해야 한다."""

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tourapi.place")
    altered_license = source.license.model_copy(update={"terms_fingerprint": "PENDING_REVIEW"})
    altered = source.model_copy(update={"license": altered_license})
    result = SourcePreflight().check(altered, {"JEJU_TOURAPI_SERVICE_KEY": "test-key"})
    assert result.status == "FAIL"
    assert result.reason_codes == ("SOURCE_TERMS_EVIDENCE_INVALID",)


def test_tmap_preflight_preserves_memory_only_retention() -> None:
    """TMAP preflight는 영속 저장 금지와 24시간 미만 정책을 검증해야 한다."""

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tmap.pedestrian")
    result = SourcePreflight().check(source, {"JEJU_TMAP_API_KEY": "test-key"})
    assert result.status == "PASS"
    assert "MEMORY_ONLY_RETENTION" in result.evidence


def test_tago_preflight_does_not_mislabel_memory_retention_as_tmap() -> None:
    """TAGO의 메모리 전용 보존 근거는 다른 provider 이름으로 잘못 표시하지 않아야 한다."""

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require(
        "tago.bus-arrival"
    )
    result = SourcePreflight().check(source, {"JEJU_TAGO_SERVICE_KEY": "test-key"})

    assert "MEMORY_ONLY_RETENTION" in result.evidence
    assert all("TMAP" not in item for item in result.evidence)
