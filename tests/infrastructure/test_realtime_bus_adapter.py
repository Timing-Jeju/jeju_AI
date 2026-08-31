"""TAGO 실시간 버스 도착 adapter 테스트."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jeju_trip.infrastructure.public_data_http import FetchedJson
from jeju_trip.infrastructure.realtime_bus_adapter import RealtimeBusArrivalAdapter
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))


class FakePageClient:
    def __init__(self, retrieved_at: datetime, *, include_city_code: bool = True) -> None:
        self.retrieved_at = retrieved_at
        self.include_city_code = include_city_code

    def fetch_json(self, contract, endpoint, query, environment):
        item = {
            "nodeid": "JJB500000001",
            "routeid": "JJB394000101",
            "routeno": "101",
            "arrtime": 120,
            "arrprevstationcnt": 2,
            "vehicletp": "일반차량",
        }
        if self.include_city_code:
            item["citycode"] = "39"
        document = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "OK"},
                "body": {
                    "pageNo": 1,
                    "numOfRows": 10,
                    "totalCount": 1,
                    "items": {"item": item},
                },
            }
        }
        raw = json.dumps(document).encode()
        return FetchedJson(raw_bytes=raw, document=document, retrieved_at=self.retrieved_at)


def test_realtime_arrival_expires_after_sixty_seconds() -> None:
    """TAGO 도착정보 snapshot은 조회 후 60초가 지나면 stale이어야 한다."""

    checked_at = datetime(2026, 8, 15, 12, 20, tzinfo=KST)
    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-arrival")
    snapshot = RealtimeBusArrivalAdapter(FakePageClient(checked_at)).fetch(
        contract,
        city_code="39",
        node_id="JJB500000001",
        environment={"JEJU_TAGO_SERVICE_KEY": "secret"},
    )
    assert snapshot.is_stale(checked_at + timedelta(seconds=60)) is False
    assert snapshot.is_stale(checked_at + timedelta(seconds=61)) is True
    assert snapshot.arrivals[0].expected_arrival_at == checked_at + timedelta(seconds=120)
    assert "secret" not in repr(snapshot)


def test_realtime_arrival_rejects_snapshot_from_future_request_time() -> None:
    """과거 계획 시각 재판정에는 그 이후에 조회한 TAGO snapshot을 적용하지 않아야 한다."""

    checked_at = datetime(2026, 8, 15, 12, 20, tzinfo=KST)
    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-arrival")
    snapshot = RealtimeBusArrivalAdapter(FakePageClient(checked_at)).fetch(
        contract,
        city_code="39",
        node_id="JJB500000001",
        environment={"JEJU_TAGO_SERVICE_KEY": "secret"},
    )

    assert snapshot.is_stale(checked_at - timedelta(seconds=6)) is True
    assert snapshot.is_stale(checked_at - timedelta(seconds=5)) is False


def test_realtime_arrival_requires_confirmed_route_mapping() -> None:
    """실시간 노선 ID는 CONFIRMED mapping일 때만 계획 노선에 적용해야 한다."""

    checked_at = datetime(2026, 8, 15, 12, 20, tzinfo=KST)
    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-arrival")
    snapshot = RealtimeBusArrivalAdapter(FakePageClient(checked_at)).fetch(
        contract,
        city_code="39",
        node_id="JJB500000001",
        environment={"JEJU_TAGO_SERVICE_KEY": "secret"},
    )
    with pytest.raises(ValueError, match="ROUTE_MAPPING_UNCONFIRMED"):
        snapshot.for_planned_route("JJB394000101", "REVIEW_REQUIRED")
    assert snapshot.for_planned_route("JJB394000101", "CONFIRMED").route_number == "101"


def test_realtime_arrival_uses_requested_city_code_when_provider_omits_it() -> None:
    """TAGO 응답에 도시코드가 없어도 요청에 사용한 제주 도시코드로 정규화해야 한다."""

    checked_at = datetime(2026, 8, 15, 12, 20, tzinfo=KST)
    contract = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-arrival")
    snapshot = RealtimeBusArrivalAdapter(FakePageClient(checked_at, include_city_code=False)).fetch(
        contract,
        city_code="39",
        node_id="JJB500000001",
        environment={"JEJU_TAGO_SERVICE_KEY": "secret"},
    )

    assert snapshot.arrivals[0].city_code == "39"
