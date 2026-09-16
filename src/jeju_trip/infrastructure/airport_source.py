"""한국공항공사 원본의 공항 대표점을 보존한다. 검증된 출입구는 만들지 않는다."""

from __future__ import annotations

import csv
import io
from typing import Literal

from jeju_trip.domain.models import Coordinates
from jeju_trip.infrastructure.public_data_normalizers import NormalizedBatch, ProjectionRecord
from jeju_trip.planning.quality import preliminary_jeju_coordinate_check


class AirportPlaceRecord(ProjectionRecord):
    """공통 장소 projection 형식을 재사용하되 TourAPI 분류·ID는 사용하지 않는다."""

    content_type_id: Literal["airport"] = "airport"
    fact_id: Literal["kac.airport:CJU"] = "kac.airport:CJU"
    source_record_id: Literal["제주"] = "제주"
    name: Literal["제주국제공항"] = "제주국제공항"
    address: str
    position: Coordinates
    category_level_1: None = None
    category_level_2: None = None
    category_level_3: None = None


def normalize_airport_csv(raw: bytes) -> NormalizedBatch[AirportPlaceRecord]:
    """제주라는 원본 식별자를 운영 canonical CJU에 명시적으로 대응한다."""
    rows = list(csv.DictReader(io.StringIO(raw.decode("cp949"))))
    selected = [row for row in rows if row.get("공항명", "").strip() == "제주"]
    if len(selected) != 1:
        raise ValueError("AIRPORT_IDENTITY_NOT_UNIQUE")
    row = selected[0]
    position = Coordinates(
        latitude=float(row["위도(WGS84좌표)"]), longitude=float(row["경도(WGS84좌표)"])
    )
    if preliminary_jeju_coordinate_check(position.latitude, position.longitude):
        raise ValueError("AIRPORT_COORDINATE_OUTSIDE_JEJU")
    address = row["행정구역"].strip()
    if not address or "제주" not in address:
        raise ValueError("AIRPORT_ADDRESS_INVALID")
    return NormalizedBatch(
        records=(
            AirportPlaceRecord(
                fact_id="kac.airport:CJU",
                source_record_id="제주",
                name="제주국제공항",
                address=address,
                position=position,
            ),
        ),
        rejections=(),
    )
