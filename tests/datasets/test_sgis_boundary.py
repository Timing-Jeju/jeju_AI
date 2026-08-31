"""공식 SGIS 제주 경계 ZIP 정규화 테스트."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from typing import Any, cast

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Point, shape

from jeju_trip.infrastructure.sgis_boundary import (
    OFFICIAL_SGIS_BOUNDARY_SHA256,
    normalize_sgis_jeju_boundary_zip,
)


def _boundary_zip(*, sido_code: str = "39", cpg: bytes = b"UTF-8") -> bytes:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    ring = [
        transformer.transform(126.0, 33.0),
        transformer.transform(127.0, 33.0),
        transformer.transform(127.0, 33.7),
        transformer.transform(126.0, 33.7),
        transformer.transform(126.0, 33.0),
    ]
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as writer:
        typed_writer = cast(Any, writer)
        typed_writer.field("BASE_DATE", "C", size=8)
        typed_writer.field("SIDO_CD", "C", size=2)
        typed_writer.field("SIDO_NM", "C", size=25)
        writer.poly([ring])
        writer.record("20250630", sido_code, "제주특별자치도")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        stem = "nested/bnd_sido_00_2025_2Q"
        bundle.writestr(f"{stem}.cpg", cpg)
        bundle.writestr(f"{stem}.prj", CRS.from_epsg(5179).to_wkt())
        bundle.writestr(f"{stem}.shp", shp.getvalue())
        bundle.writestr(f"{stem}.shx", shx.getvalue())
        bundle.writestr(f"{stem}.dbf", dbf.getvalue())
        bundle.writestr("national-statistics.csv", "읽지 않아야 하는 파일")
    return archive.getvalue()


def test_sgis_zip_extracts_only_jeju_and_transforms_without_simplification() -> None:
    """전국 ZIP에서 제주 행 하나만 골라 원 좌표점을 보존한 WGS84 MultiPolygon으로 바꿔야 한다."""

    raw = _boundary_zip()
    result = normalize_sgis_jeju_boundary_zip(
        raw,
        source_date=date(2025, 6, 30),
        expected_checksum=hashlib.sha256(raw).hexdigest(),
    )

    assert not result.rejections
    assert len(result.records) == 1
    geometry = shape(result.records[0].geometry)
    assert isinstance(geometry, MultiPolygon)
    assert geometry.geom_type == "MultiPolygon"
    assert geometry.is_valid
    assert geometry.covers(Point(126.5, 33.3))
    assert len(geometry.geoms[0].exterior.coords) == 5


def test_sgis_zip_rejects_wrong_checksum_before_shape_parsing() -> None:
    """공식 ZIP checksum이 다르면 SHP 내용을 신뢰하거나 정규화하지 않아야 한다."""

    result = normalize_sgis_jeju_boundary_zip(
        b"not-a-zip",
        source_date=date(2025, 6, 30),
        expected_checksum=OFFICIAL_SGIS_BOUNDARY_SHA256,
    )

    assert result.records == ()
    assert result.rejections[0].reason_code == "BOUNDARY_ARCHIVE_CHECKSUM_MISMATCH"


def test_sgis_zip_rejects_non_utf8_cpg_and_wrong_jeju_code() -> None:
    """SHP 문자셋과 제주 행 식별자가 공식 계약과 다르면 publication 후보를 만들지 않아야 한다."""

    raw = _boundary_zip(sido_code="38", cpg=b"CP949")
    result = normalize_sgis_jeju_boundary_zip(
        raw,
        source_date=date(2025, 6, 30),
        expected_checksum=hashlib.sha256(raw).hexdigest(),
    )

    assert result.records == ()
    assert result.rejections[0].reason_code == "BOUNDARY_CPG_INVALID"
