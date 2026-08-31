"""공식 API refresh profile 계약 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from jeju_trip.infrastructure.refresh_profiles import RefreshProfileCatalog
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[2]


def _catalog() -> RefreshProfileCatalog:
    sources = SourceCatalog.load(ROOT / "config/data_sources.toml")
    return RefreshProfileCatalog.load(ROOT / "config/refresh_profiles.toml", sources)


def test_every_refresh_profile_uses_an_approved_source() -> None:
    """모든 자동 수집 profile은 승인된 source ID와 유효한 endpoint만 사용해야 한다."""

    catalog = _catalog()
    assert {profile.source_id for profile in catalog.profiles} == {
        "tourapi.place",
        "tago.bus-route",
        "tago.bus-route-stops",
        "holiday.special-day",
        "tago.bus-stop",
        "tourapi.place-intro",
    }
    assert all(profile.endpoint and "/" not in profile.endpoint for profile in catalog.profiles)


def test_required_runtime_query_is_never_guessed() -> None:
    """공휴일 연도 같은 실행값은 누락 시 임의 추정하지 않아야 한다."""

    profile = _catalog().require("holiday-special-days")
    with pytest.raises(ValueError, match="REFRESH_PARAMETERS_MISSING:solYear"):
        profile.materialize_query({})
    assert profile.materialize_query({"solYear": "2026"})["solYear"] == "2026"


def test_tago_stop_profile_collects_the_whole_jeju_city_code() -> None:
    """제주 전역 정류장은 500m 단일 좌표 검색이 아니라 도시코드 전체 목록으로 수집해야 한다."""

    profile = _catalog().require("tago-jeju-bus-stops")
    assert profile.endpoint == "getSttnNoList"
    assert profile.required_parameters == ()
    assert profile.query == {"cityCode": "39"}


def test_tago_route_stop_profile_requires_the_complete_jeju_route_bundle() -> None:
    """제주 전역 경유정류소는 route ID 전체 묶음을 aggregate snapshot으로 받아야 한다."""

    profile = _catalog().require("tago-jeju-bus-route-stops")

    assert profile.endpoint == "getRouteAcctoThrghSttnList"
    assert profile.required_parameters == ("routeId",)
    assert profile.snapshot_mode == "aggregate"
    assert profile.query == {"cityCode": "39"}


def test_tourapi_intro_profile_requires_explicit_place_identity() -> None:
    """TourAPI 상세소개는 장소 ID와 콘텐츠 유형을 추정하지 않고 명시적으로 받아야 한다."""

    profile = _catalog().require("tourapi-jeju-place-intro-hours")
    with pytest.raises(ValueError, match="contentId,contentTypeId"):
        profile.materialize_query({})
    query = profile.materialize_query({"contentId": "123", "contentTypeId": "12"})
    assert query["contentId"] == "123"


def test_partial_query_profiles_require_aggregate_publication() -> None:
    """장소별·연도별 API는 단건 결과를 완전 snapshot처럼 활성화하지 않아야 한다."""

    catalog = _catalog()
    assert catalog.require("tourapi-jeju-place-intro-hours").snapshot_mode == "aggregate"
    assert catalog.require("holiday-special-days").snapshot_mode == "aggregate"
    assert catalog.require("tourapi-jeju-places").snapshot_mode == "complete"
