"""승인 검증과 메모리 cache 뒤에서만 TMAP을 호출하는 adapter."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

import httpx

from jeju_trip.infrastructure.source_catalog import TravelSourceContract
from jeju_trip.infrastructure.tmap_cache import EphemeralRouteCache, RouteCacheKey


class JsonHttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        maximum_response_bytes: int,
    ) -> dict[str, Any]: ...


class TmapRequestFailed(RuntimeError):
    """원문·좌표·외부 오류 문구를 노출하지 않는 TMAP 호출 실패."""


class HttpxJsonTransport:
    """redirect와 과대 응답을 거부하고 오류 본문을 노출하지 않는 JSON transport."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        maximum_response_bytes: int,
    ) -> dict[str, Any]:
        with self._client.stream(
            "POST",
            url,
            json=payload,
            headers=headers,
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise ValueError("TMAP_REDIRECT_REJECTED")
            if response.status_code != 200:
                raise ValueError(f"TMAP_HTTP_STATUS_{response.status_code}")
            declared = response.headers.get("content-length")
            if declared and int(declared) > maximum_response_bytes:
                raise ValueError("TMAP_RESPONSE_TOO_LARGE")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > maximum_response_bytes:
                    raise ValueError("TMAP_RESPONSE_TOO_LARGE")
        try:
            document = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("TMAP_JSON_INVALID") from error
        if not isinstance(document, dict):
            raise ValueError("TMAP_JSON_ROOT_INVALID")
        return document


@dataclass(frozen=True)
class SafeRouteLog:
    request_id: str
    source_id: str
    request_fingerprint: str
    retrieved_at: datetime
    expires_at: datetime
    latency_ms: int
    status: str
    safe_reason_code: str | None


@dataclass(frozen=True)
class PedestrianRouteFact:
    route_fact_id: str
    origin_entrance_id: str
    destination_entrance_id: str
    distance_meters: int
    expected_minutes: int
    stairs_status: str
    slope_status: str


@dataclass(frozen=True)
class DrivingRouteFact:
    route_fact_id: str
    origin_entrance_id: str
    destination_entrance_id: str
    distance_meters: int
    duration_seconds: int
    toll_fare_krw: int


def _summary_properties(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        features = raw["features"]
        if not isinstance(features, list) or not features:
            raise ValueError
        properties = features[0]["properties"]
        if not isinstance(properties, dict):
            raise ValueError
        return properties
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("TMAP_ROUTE_SUMMARY_MISSING") from error


def _nonnegative_int(properties: dict[str, Any], field: str) -> int:
    try:
        value = int(properties[field])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"TMAP_ROUTE_FIELD_INVALID:{field}") from error
    if value < 0:
        raise ValueError(f"TMAP_ROUTE_FIELD_NEGATIVE:{field}")
    return value


def normalize_tmap_pedestrian(
    raw: dict[str, Any],
    *,
    origin_entrance_id: str,
    destination_entrance_id: str,
    route_fact_id: str,
) -> PedestrianRouteFact:
    """상세 geometry를 복사하지 않고 보행 요약 fact만 만든다."""

    properties = _summary_properties(raw)
    distance = _nonnegative_int(properties, "totalDistance")
    duration_seconds = _nonnegative_int(properties, "totalTime")
    return PedestrianRouteFact(
        route_fact_id=route_fact_id,
        origin_entrance_id=origin_entrance_id,
        destination_entrance_id=destination_entrance_id,
        distance_meters=distance,
        expected_minutes=math.ceil(duration_seconds / 60),
        stairs_status="UNKNOWN",
        slope_status="UNKNOWN",
    )


def normalize_tmap_driving(
    raw: dict[str, Any],
    *,
    origin_entrance_id: str,
    destination_entrance_id: str,
    route_fact_id: str,
) -> DrivingRouteFact:
    """상세 geometry와 TMAP 자체 택시 추정값을 버리고 차량 요약 fact만 만든다."""

    properties = _summary_properties(raw)
    return DrivingRouteFact(
        route_fact_id=route_fact_id,
        origin_entrance_id=origin_entrance_id,
        destination_entrance_id=destination_entrance_id,
        distance_meters=_nonnegative_int(properties, "totalDistance"),
        duration_seconds=_nonnegative_int(properties, "totalTime"),
        toll_fare_krw=_nonnegative_int(properties, "totalFare"),
    )


class TmapAdapter[T]:
    def __init__(
        self,
        contract: TravelSourceContract,
        cache: EphemeralRouteCache[T],
        transport: JsonHttpTransport,
        normalizer: Callable[[dict[str, Any]], T],
        api_key: str,
        safe_log: Callable[[SafeRouteLog], None],
    ) -> None:
        if contract.id not in {"tmap.pedestrian", "tmap.driving"}:
            raise ValueError("TMAP_SOURCE_CONTRACT_REQUIRED")
        self._contract = contract
        self._cache = cache
        self._transport = transport
        self._normalizer = normalizer
        self._api_key = api_key
        self._safe_log = safe_log

    def fetch(self, key: RouteCacheKey, payload: dict[str, Any]) -> T:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        url = self._contract.acquisition.base_url
        self._contract.assert_network_request_allowed(url)
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        request_id = f"route-{uuid4()}"
        retrieved_at = datetime.now(UTC)
        expires_at = retrieved_at + timedelta(seconds=self._cache.ttl_seconds)
        started_at = time.perf_counter()
        try:
            raw = self._transport.post_json(
                url,
                payload,
                {"appKey": self._api_key},
                self._contract.acquisition.maximum_response_bytes,
            )
            normalized = self._normalizer(raw)
            self._cache.put(key, normalized)
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            self._safe_log(
                SafeRouteLog(
                    request_id,
                    self._contract.id,
                    fingerprint,
                    retrieved_at,
                    expires_at,
                    latency_ms,
                    "PASS",
                    None,
                )
            )
            return normalized
        except Exception:
            latency_ms = round((time.perf_counter() - started_at) * 1000)
            self._safe_log(
                SafeRouteLog(
                    request_id,
                    self._contract.id,
                    fingerprint,
                    retrieved_at,
                    expires_at,
                    latency_ms,
                    "FAIL",
                    "TMAP_REQUEST_FAILED",
                )
            )
            raise TmapRequestFailed("TMAP_REQUEST_FAILED") from None
