"""TMAP 응답을 파일이나 DB 없이 프로세스 메모리에만 두는 TTL cache."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_TTL_SECONDS = 23 * 60 * 60 + 50 * 60


@dataclass(frozen=True)
class RouteCacheKey:
    source_id: str
    origin_latitude: float
    origin_longitude: float
    destination_latitude: float
    destination_longitude: float
    departure_minute: str
    allowed_modes: tuple[str, ...]
    walking_profile: str
    request_schema_version: str

    @classmethod
    def build(
        cls,
        source_id: str,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_minute: str,
        allowed_modes: set[str],
        walking_profile: str,
        request_schema_version: str,
    ) -> RouteCacheKey:
        return cls(
            source_id=source_id,
            origin_latitude=round(origin[0], 6),
            origin_longitude=round(origin[1], 6),
            destination_latitude=round(destination[0], 6),
            destination_longitude=round(destination[1], 6),
            departure_minute=departure_minute,
            allowed_modes=tuple(sorted(allowed_modes)),
            walking_profile=walking_profile,
            request_schema_version=request_schema_version,
        )


class EphemeralRouteCache[T]:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        if ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
            raise ValueError("TMAP_CACHE_TTL_OUT_OF_RANGE")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._items: dict[RouteCacheKey, tuple[float, T]] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def get(self, key: RouteCacheKey) -> T | None:
        now = self._clock()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            return value

    def put(self, key: RouteCacheKey, value: T) -> None:
        with self._lock:
            self._items[key] = (self._clock() + self._ttl_seconds, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
