"""TMAP 메모리 TTL cache 테스트."""

from __future__ import annotations

import pytest

from jeju_trip.infrastructure.tmap_cache import (
    MAX_TTL_SECONDS,
    EphemeralRouteCache,
    RouteCacheKey,
)


def _key() -> RouteCacheKey:
    return RouteCacheKey.build(
        source_id="tmap.pedestrian",
        origin=(33.5104004, 126.4912999),
        destination=(33.543, 126.669),
        departure_minute="2026-08-15T09:00+09:00",
        allowed_modes={"walk"},
        walking_profile="standard",
        request_schema_version="v1",
    )


def test_tmap_cache_expires_before_storage_limit() -> None:
    """TMAP 캐시는 약관 제한보다 이른 최대 23시간 50분 안에 만료되어야 한다."""

    now = [100.0]
    cache = EphemeralRouteCache[str](10, clock=lambda: now[0])
    cache.put(_key(), "normalized-route")
    now[0] = 111.0
    assert cache.get(_key()) is None


def test_tmap_cache_rejects_excessive_ttl() -> None:
    """TMAP cache TTL을 23시간 50분보다 길게 설정할 수 없어야 한다."""

    with pytest.raises(ValueError, match="TTL"):
        EphemeralRouteCache[str](MAX_TTL_SECONDS + 1)


def test_cache_key_rounds_coordinates_and_excludes_api_key() -> None:
    """cache key는 좌표를 6자리로 고정하고 API key를 포함하지 않아야 한다."""

    key = _key()
    assert key.origin_latitude == 33.5104
    assert "api_key" not in key.__dataclass_fields__
    assert "authorization" not in key.__dataclass_fields__
