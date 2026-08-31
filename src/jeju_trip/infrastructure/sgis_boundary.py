"""공식 SGIS 시도 경계 ZIP을 제주 WGS84 projection record로 변환한다."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import date
from pathlib import PurePosixPath
from typing import Any

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform

from jeju_trip.infrastructure.normalization_spool import Rejection
from jeju_trip.infrastructure.public_data_normalizers import JejuBoundaryRecord, NormalizedBatch

OFFICIAL_SGIS_BOUNDARY_SHA256 = "f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6"
OFFICIAL_SGIS_BOUNDARY_SOURCE_DATE = date(2025, 6, 30)
OFFICIAL_SGIS_BOUNDARY_REFERENCE = "https://www.data.go.kr/data/15129688/fileData.do"
_STEM_PATTERN = re.compile(r"^bnd_sido_00_\d{4}_\dQ$")
_REQUIRED_EXTENSIONS = frozenset({".cpg", ".prj", ".shp", ".shx", ".dbf"})


class _BoundaryArchiveError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def assert_official_sgis_boundary_checksum(raw: bytes) -> None:
    """원본을 저장하기 전 공식 파일과 byte-for-byte 일치하는지 확인한다."""

    if hashlib.sha256(raw).hexdigest() != OFFICIAL_SGIS_BOUNDARY_SHA256:
        raise ValueError("BOUNDARY_ARCHIVE_CHECKSUM_MISMATCH")


def _archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    candidates: dict[str, dict[str, zipfile.ZipInfo]] = {}
    for member in archive.infolist():
        basename = PurePosixPath(member.filename).name
        suffix = PurePosixPath(basename).suffix.lower()
        stem = PurePosixPath(basename).stem
        if _STEM_PATTERN.fullmatch(stem) and suffix in _REQUIRED_EXTENSIONS:
            if suffix in candidates.setdefault(stem, {}):
                raise _BoundaryArchiveError("BOUNDARY_SHAPEFILE_MEMBER_DUPLICATED")
            candidates[stem][suffix] = member
    complete = {
        stem: members
        for stem, members in candidates.items()
        if set(members) == _REQUIRED_EXTENSIONS
    }
    if len(complete) != 1 or len(candidates) != 1:
        raise _BoundaryArchiveError("BOUNDARY_SHAPEFILE_BUNDLE_INVALID")
    return next(iter(complete.values()))


def _read_jeju_geometry(
    archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo]
) -> dict[str, Any]:
    if archive.read(members[".cpg"]).decode("ascii").strip().upper().replace("-", "") != "UTF8":
        raise _BoundaryArchiveError("BOUNDARY_CPG_INVALID")
    try:
        source_crs = CRS.from_wkt(archive.read(members[".prj"]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _BoundaryArchiveError("BOUNDARY_PRJ_INVALID") from error
    # SGIS는 EPSG 식별자가 없는 ESRI WKT를 배포하지만 PROJ가 동일 매개변수를 5179로 식별한다.
    if source_crs.to_epsg() != 5179:
        raise _BoundaryArchiveError("BOUNDARY_CRS_NOT_EPSG_5179")

    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read(members[".shp"])),
        shx=io.BytesIO(archive.read(members[".shx"])),
        dbf=io.BytesIO(archive.read(members[".dbf"])),
        encoding="utf-8",
    )
    try:
        jeju_rows = []
        for item in reader.iterShapeRecords():
            if item.record is None or item.shape is None:
                raise _BoundaryArchiveError("BOUNDARY_SHAPEFILE_RECORD_INVALID")
            if str(item.record.as_dict().get("SIDO_CD", "")).strip() == "39":
                jeju_rows.append(item)
        if len(jeju_rows) != 1:
            raise _BoundaryArchiveError("BOUNDARY_JEJU_RECORD_COUNT_INVALID")
        record = jeju_rows[0]
        if record.record is None or record.shape is None:
            raise _BoundaryArchiveError("BOUNDARY_SHAPEFILE_RECORD_INVALID")
        attributes = record.record.as_dict()
        if str(attributes.get("SIDO_NM", "")).strip() != "제주특별자치도":
            raise _BoundaryArchiveError("BOUNDARY_JEJU_NAME_INVALID")
        if str(attributes.get("BASE_DATE", "")).strip() != "20250630":
            raise _BoundaryArchiveError("BOUNDARY_BASE_DATE_INVALID")
        source_geometry = shape(record.shape.__geo_interface__)
    finally:
        reader.close()

    if not isinstance(source_geometry, Polygon | MultiPolygon) or source_geometry.is_empty:
        raise _BoundaryArchiveError("BOUNDARY_GEOMETRY_INVALID")
    transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    converted = transform(transformer.transform, source_geometry)
    if isinstance(converted, Polygon):
        converted = MultiPolygon([converted])
    if not isinstance(converted, MultiPolygon) or converted.is_empty or not converted.is_valid:
        raise _BoundaryArchiveError("BOUNDARY_GEOMETRY_INVALID")
    min_x, min_y, max_x, max_y = converted.bounds
    if not (125.0 <= min_x <= max_x <= 127.5 and 32.5 <= min_y <= max_y <= 34.5):
        raise _BoundaryArchiveError("BOUNDARY_OUTSIDE_JEJU")
    return dict(mapping(converted))


def normalize_sgis_jeju_boundary_zip(
    raw: bytes,
    *,
    source_date: date,
    expected_checksum: str = OFFICIAL_SGIS_BOUNDARY_SHA256,
) -> NormalizedBatch[JejuBoundaryRecord]:
    """checksum·SHP metadata를 검증하고 제주 record 하나만 WGS84로 변환한다."""

    try:
        if hashlib.sha256(raw).hexdigest() != expected_checksum:
            raise _BoundaryArchiveError("BOUNDARY_ARCHIVE_CHECKSUM_MISMATCH")
        if source_date != OFFICIAL_SGIS_BOUNDARY_SOURCE_DATE:
            raise _BoundaryArchiveError("BOUNDARY_SOURCE_DATE_MISMATCH")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = _archive_members(archive)
            geometry = _read_jeju_geometry(archive, members)
        return NormalizedBatch(
            records=(
                JejuBoundaryRecord(
                    fact_id="spatial.jeju-boundary:39:20250630",
                    boundary_id="jeju-all",
                    name="제주특별자치도",
                    geometry=geometry,
                    source_reference=OFFICIAL_SGIS_BOUNDARY_REFERENCE,
                ),
            ),
            rejections=(),
        )
    except _BoundaryArchiveError as error:
        return NormalizedBatch((), (Rejection(None, "archive", error.reason_code),))
    except (UnicodeDecodeError, zipfile.BadZipFile, shapefile.ShapefileException, ValueError):
        return NormalizedBatch((), (Rejection(None, "archive", "BOUNDARY_ARCHIVE_INVALID"),))
