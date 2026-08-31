"""공식 XLSX importer의 fail-closed 안전 규칙을 검증한다."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

import pytest

import jeju_trip.infrastructure.timetable_xlsx as timetable_xlsx
from jeju_trip.infrastructure.timetable_xlsx import (
    TimetableWorkbookRejected,
    WorkbookCellRow,
    _canonical_json_sha256,
    _validate_snapshot_checksum_manifest,
    _validate_snapshot_mapping_lineage,
    load_official_timetable_zip,
    read_official_workbook,
    require_holiday_service_evidence,
    service_day_source_references,
)


def _workbook_with_incorrect_dimension() -> bytes:
    """공식 BIS 파일처럼 dimension만 A1로 축소된 유효 workbook fixture를 만든다."""

    from openpyxl import Workbook

    workbook_stream = io.BytesIO()
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "201 제주터미널-성산"
    worksheet["B7"] = "제주버스터미널"
    worksheet["C8"] = "06:00"
    workbook.save(workbook_stream)
    source = zipfile.ZipFile(io.BytesIO(workbook_stream.getvalue()))
    target_stream = io.BytesIO()
    with zipfile.ZipFile(target_stream, "w") as target:
        for member in source.infolist():
            payload = source.read(member)
            if member.filename == "xl/worksheets/sheet1.xml":
                payload = re.sub(rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1"/>', payload)
            target.writestr(member, payload)
    return target_stream.getvalue()


def test_read_only_importer_recovers_incorrect_official_dimension() -> None:
    """BIS workbook의 잘못된 A1 dimension에도 실제 주요 정류장 셀을 읽어야 한다."""

    payload = _workbook_with_incorrect_dimension()
    rows = read_official_workbook(
        io.BytesIO(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        required_sheet_tokens=("201",),
    )

    assert any(row.row_number == 8 and "06:00" in row.values for row in rows)


def test_holiday_calendar_requires_official_https_evidence() -> None:
    """광복절 서비스데이는 공식 HTTPS 근거가 없으면 생성하지 않아야 한다."""

    with pytest.raises(TimetableWorkbookRejected, match="BUS_SERVICE_DAY_UNVERIFIED"):
        require_holiday_service_evidence(())


def test_weekday_timetable_does_not_require_holiday_evidence() -> None:
    """평일 일반 시간표는 공휴일 근거가 없어도 공식 평일 출처만으로 검증돼야 한다."""

    references = service_day_source_references(
        {
            "weekday_source_references": [
                "https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule"
            ],
            "holiday_source_references": [],
        }
    )

    assert references == ("https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule",)


def test_weekday_timetable_requires_official_https_evidence() -> None:
    """평일 시간표도 공식 HTTPS 출처 없이 임의로 발행할 수 없어야 한다."""

    with pytest.raises(TimetableWorkbookRejected, match="BUS_WEEKDAY_SERVICE_DAY_UNVERIFIED"):
        service_day_source_references(
            {"weekday_source_references": [], "holiday_source_references": []}
        )


def test_raw_timetable_zip_requires_all_lineage_manifests() -> None:
    """공식 시간표 raw ZIP은 checksum·mapping·service-day manifest를 모두 포함해야 한다."""

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("201.xlsx", b"not-a-workbook")
    stream.seek(0)
    with pytest.raises(TimetableWorkbookRejected, match="TIMETABLE_RAW_MANIFEST_MISSING"):
        load_official_timetable_zip(stream)


def test_global_snapshot_requires_the_pinned_complete_checksum_manifest(monkeypatch) -> None:
    """전역 raw ZIP은 알려진 snapshot의 전체 checksum manifest와 정확히 같아야 한다."""

    manifest = {"algorithm": "SHA256", "files": {"405001.xlsx": "a" * 64}}
    monkeypatch.setattr(
        timetable_xlsx,
        "OFFICIAL_SNAPSHOT_CHECKSUM_MANIFEST_SHA256",
        {"fixture": _canonical_json_sha256(manifest)},
    )

    assert _validate_snapshot_checksum_manifest("fixture", manifest) is True
    with pytest.raises(
        TimetableWorkbookRejected,
        match="TIMETABLE_OFFICIAL_SNAPSHOT_CHECKSUM_MISMATCH",
    ):
        _validate_snapshot_checksum_manifest(
            "fixture",
            {"algorithm": "SHA256", "files": {"405001.xlsx": "b" * 64}},
        )


def test_global_mapping_lineage_rejects_clock_not_present_in_source_row() -> None:
    """derived stop time은 pinned workbook 원본 행에 같은 순서로 존재해야 한다."""

    rows = {
        "405001.xlsx": (
            WorkbookCellRow("101 출발-도착", 8, (None, 1, "09:00", "09:40")),
        )
    }
    stop_times: list[dict[str, object]] = [
        {
            "provider_stop_id": "stop-a",
            "route_sequence": 1,
            "arrival_time": "0900",
            "departure_time": "0900",
        },
        {
            "provider_stop_id": "stop-b",
            "route_sequence": 2,
            "arrival_time": "0940",
            "departure_time": "0940",
        },
    ]
    manifest: dict[str, object] = {
        "route_stops": [
            {
                "provider_route_pattern_id": "pattern-a",
                "provider_stop_id": "stop-a",
                "route_sequence": 1,
            },
            {
                "provider_route_pattern_id": "pattern-a",
                "provider_stop_id": "stop-b",
                "route_sequence": 2,
            },
        ],
        "trip_rows": [
            {
                "workbook": "405001.xlsx",
                "sheet_name": "101 출발-도착",
                "source_row_number": 8,
                "provider_route_pattern_ids": ["pattern-a"],
                "major_stop_times": stop_times,
            }
        ],
    }

    _validate_snapshot_mapping_lineage(rows, manifest)
    stop_times[1]["arrival_time"] = "0950"
    stop_times[1]["departure_time"] = "0950"
    with pytest.raises(TimetableWorkbookRejected, match="TIMETABLE_SOURCE_CLOCK_MISMATCH"):
        _validate_snapshot_mapping_lineage(rows, manifest)
