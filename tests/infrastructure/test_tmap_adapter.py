"""TMAP adapter의 안전한 호출·로그 테스트."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.infrastructure.tmap_adapter import (
    HttpxJsonTransport,
    SafeRouteLog,
    TmapAdapter,
    normalize_tmap_driving,
    normalize_tmap_pedestrian,
)
from jeju_trip.infrastructure.tmap_cache import EphemeralRouteCache, RouteCacheKey

ROOT = Path(__file__).resolve().parents[2]


class FakeTransport:
    def __init__(self) -> None:
        self.calls = 0

    def post_json(self, url, payload, headers, maximum_response_bytes):
        self.calls += 1
        assert headers == {"appKey": "secret-api-key"}
        return {"raw_secret_field": "must-not-be-logged", "duration": 20}


def test_tmap_adapter_logs_only_safe_metadata_and_reuses_memory_cache() -> None:
    """TMAP adapter는 raw body 없이 안전한 메타데이터만 로그하고 메모리 cache를 재사용해야 한다."""

    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tmap.pedestrian")
    cache = EphemeralRouteCache[dict[str, int]](60)
    transport = FakeTransport()
    logs: list[SafeRouteLog] = []
    adapter = TmapAdapter(
        contract,
        cache,
        transport,
        lambda raw: {"duration_minutes": int(raw["duration"])},
        "secret-api-key",
        logs.append,
    )
    key = RouteCacheKey.build(
        "tmap.pedestrian",
        (33.51, 126.49),
        (33.54, 126.67),
        "2026-08-15T09:00+09:00",
        {"walk", "bus"},
        "standard",
        "v1",
    )
    first = adapter.fetch(key, {"origin": "redacted", "destination": "redacted"})
    second = adapter.fetch(key, {"origin": "redacted", "destination": "redacted"})
    assert first == second == {"duration_minutes": 20}
    assert transport.calls == 1
    assert len(logs) == 1
    assert "secret-api-key" not in repr(logs[0])
    assert "raw_secret_field" not in repr(logs[0])


def test_tmap_pedestrian_normalizer_discards_geometry() -> None:
    """보행 정규화 결과는 시간·거리만 남기고 상세 geometry를 폐기해야 한다."""

    result = normalize_tmap_pedestrian(
        {
            "features": [
                {
                    "geometry": {"type": "LineString", "coordinates": [[126.5, 33.5]]},
                    "properties": {"totalDistance": 1250, "totalTime": 900},
                }
            ]
        },
        origin_entrance_id="entrance-a",
        destination_entrance_id="entrance-b",
        route_fact_id="fact-walk-1",
    )
    assert result.distance_meters == 1250
    assert result.expected_minutes == 15
    assert result.stairs_status == "UNKNOWN"
    assert "geometry" not in repr(result)


def test_tmap_driving_normalizer_keeps_only_route_summary() -> None:
    """차량 정규화 결과는 거리·시간·통행료만 보존하고 원본 경로를 보존하지 않아야 한다."""

    result = normalize_tmap_driving(
        {
            "features": [
                {
                    "geometry": {"type": "LineString", "coordinates": [[126.5, 33.5]]},
                    "properties": {
                        "totalDistance": 24000,
                        "totalTime": 2100,
                        "totalFare": 0,
                        "taxiFare": 28500,
                    },
                }
            ]
        },
        origin_entrance_id="vehicle-a",
        destination_entrance_id="vehicle-b",
        route_fact_id="fact-driving-1",
    )
    assert result.distance_meters == 24000
    assert result.duration_seconds == 2100
    assert result.toll_fare_krw == 0
    assert "coordinates" not in repr(result)


def test_tmap_adapter_accepts_each_approved_typed_route_source() -> None:
    """TMAP adapter는 실제 엔진이 소비하는 보행·차량 source contract만 받아야 한다."""

    catalog = SourceCatalog.load(ROOT / "config/data_sources.toml")
    for source_id in ("tmap.pedestrian", "tmap.driving"):
        TmapAdapter(
            catalog.require(source_id),
            EphemeralRouteCache[dict[str, int]](60),
            FakeTransport(),
            lambda raw: {"duration": int(raw["duration"])},
            "secret-api-key",
            lambda _: None,
        )


def test_http_transport_rejects_oversized_body_without_leaking_it() -> None:
    """TMAP HTTP 경계는 제한 초과 원문을 오류 메시지에 포함하지 않아야 한다."""

    transport = HttpxJsonTransport(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b'{"secret":"raw-body"}')
            )
        )
    )
    with pytest.raises(ValueError, match="TMAP_RESPONSE_TOO_LARGE") as captured:
        transport.post_json("https://apis.openapi.sk.com/test", {}, {}, 5)
    assert "raw-body" not in str(captured.value)


def test_http_transport_rejects_redirect_without_following_it() -> None:
    """TMAP HTTP 경계는 외부 redirect를 따라가며 좌표를 전송하지 않아야 한다."""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://evil.example/collect"})

    transport = HttpxJsonTransport(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="TMAP_REDIRECT_REJECTED"):
        transport.post_json("https://apis.openapi.sk.com/test", {}, {}, 100)
    assert calls == 1
