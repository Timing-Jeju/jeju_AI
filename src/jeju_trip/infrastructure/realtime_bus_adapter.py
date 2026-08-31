"""TAGO 실시간 도착정보를 60초 수명 typed snapshot으로 제한한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jeju_trip.infrastructure.public_data_http import parse_public_data_page
from jeju_trip.infrastructure.public_data_normalizers import (
    TagoBusArrivalRecord,
    normalize_tago_arrivals,
)
from jeju_trip.infrastructure.source_catalog import TravelSourceContract


@dataclass(frozen=True)
class RealtimeArrivalSnapshot:
    checked_at: datetime
    arrivals: tuple[TagoBusArrivalRecord, ...]
    source_id: str = "tago.bus-arrival"
    ttl_seconds: int = 60

    def is_stale(self, now: datetime) -> bool:
        age_seconds = (now - self.checked_at).total_seconds()
        return age_seconds < -5 or age_seconds > self.ttl_seconds

    def for_planned_route(
        self, provider_route_id: str, mapping_status: str
    ) -> TagoBusArrivalRecord:
        if mapping_status != "CONFIRMED":
            raise ValueError("ROUTE_MAPPING_UNCONFIRMED")
        arrival = next(
            (item for item in self.arrivals if item.provider_route_id == provider_route_id),
            None,
        )
        if arrival is None:
            raise ValueError("REALTIME_NO_DATA")
        return arrival


class RealtimeBusArrivalAdapter:
    """공식 envelope를 정규화한 뒤 raw bytes 참조를 즉시 버린다."""

    def __init__(self, page_client: Any) -> None:
        self._page_client = page_client

    def fetch(
        self,
        contract: TravelSourceContract,
        *,
        city_code: str,
        node_id: str,
        environment: dict[str, str],
    ) -> RealtimeArrivalSnapshot:
        if contract.id != "tago.bus-arrival":
            raise ValueError("TAGO_BUS_ARRIVAL_CONTRACT_REQUIRED")
        fetched = self._page_client.fetch_json(
            contract,
            "getSttnAcctoArvlPrearngeInfoList",
            {"cityCode": city_code, "nodeId": node_id, "numOfRows": "100", "pageNo": "1"},
            environment,
        )
        page = parse_public_data_page(fetched.raw_bytes)
        rows = [{**item, "citycode": item.get("citycode") or city_code} for item in page.items]
        normalized = normalize_tago_arrivals(rows, fetched.retrieved_at)
        if normalized.rejections:
            raise ValueError("REALTIME_PAYLOAD_INVALID")
        return RealtimeArrivalSnapshot(
            checked_at=fetched.retrieved_at,
            arrivals=normalized.records,
        )
