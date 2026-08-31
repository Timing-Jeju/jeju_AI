"""운영 publication과 격리된 불완전 TourAPI snapshot 분석."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from jeju_trip.infrastructure.public_data_http import parse_public_data_page
from jeju_trip.infrastructure.public_data_normalizers import (
    normalize_tour_intro_opening_rules,
)

PAGE_NAME = re.compile(r"^query-(?P<query>\d{6})-page-(?P<page>\d{6})\.json$")
HOURS_FIELDS = (
    "usetime",
    "usetimeculture",
    "usetimefestival",
    "usetimeleports",
    "usetimefood",
    "opentime",
    "opentimefood",
)
REST_FIELDS = (
    "restdate",
    "restdateculture",
    "restdatefestival",
    "restdateleports",
    "restdatefood",
    "restdateshopping",
)
CLOCK_RANGE = re.compile(
    r"(?<!\d)\d{2}:\d{2}\s*[~～-]\s*\d{2}:\d{2}(?!\d)"
)
DAY_SCOPE = re.compile(r"(?:매일|평일|주말|[월화수목금토일](?:요일)?)")
SEASONAL_OR_VARIABLE = re.compile(
    r"(?:하절기|동절기|성수기|비수기|일출|일몰|계절|시즌|"
    r"\d{1,2}\s*월?\s*[~～-]\s*\d{1,2}\s*월|"
    r"\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*[~～-]\s*"
    r"\d{4}[./-]\d{1,2}[./-]\d{1,2})"
)
NON_COLON_CLOCK = re.compile(r"\d{1,2}\s*시")
ANNUAL_OPEN_SCOPE = re.compile(r"(?:연중\s*무휴|무휴|^없(?:음|슴)?$)")
WEEKLY_CLOSED_SCOPE = re.compile(r"^매주\s*[월화수목금토일]요일$")
NO_CLOSURE_SCOPE_VARIANT = re.compile(
    r"(?:휴무(?:일)?\s*(?:없음|없다|없습니다)|쉬는\s*날\s*없음|연중\s*운영)"
)
MONTHLY_REST_SCOPE = re.compile(r"(?:매월|첫째|둘째|셋째|넷째|마지막\s*주)")
HOLIDAY_REST_SCOPE = re.compile(r"(?:공휴일|명절|설날|추석)")
IRREGULAR_REST_SCOPE = re.compile(r"(?:비정기|유동|상황|업체|전화|문의)")
WEEKLY_EXACT_LIST_SCOPE = re.compile(
    r"^매주\s*[월화수목금토일](?:요일)?"
    r"(?:\s*(?:,|·|/|및|와|과)\s*[월화수목금토일](?:요일)?)+\s*(?:휴무)?$"
)


@dataclass(frozen=True)
class PartialTourIntroAnalysis:
    status: Literal["staging_partial_only"]
    source_id: Literal["tourapi.place-intro"]
    query_scope: int
    completed_queries: int
    missing_queries: int
    completion_ratio: float
    raw_rows: int
    rows_with_hours: int
    parseable_places: int
    normalized_rules: int
    rejected_rows: int
    content_type_counts: dict[str, int]
    parseable_place_content_type_counts: dict[str, int]
    rejection_reason_counts: dict[str, int]
    rejection_content_type_reason_counts: dict[str, int]
    unverified_shape_counts: dict[str, int]
    production_activation_allowed: Literal[False] = False


def _manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        value = json.loads(archive.read("manifest.json"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("PARTIAL_MANIFEST_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("PARTIAL_MANIFEST_INVALID")
    if value.get("schema_version") != "1" or value.get("source_id") != "tourapi.place-intro":
        raise ValueError("PARTIAL_MANIFEST_SOURCE_INVALID")
    query_count = value.get("query_count")
    if not isinstance(query_count, int) or query_count < 1:
        raise ValueError("PARTIAL_MANIFEST_QUERY_COUNT_INVALID")
    return value


def _unverified_shape(row: dict[str, Any]) -> str:
    """운영시간 원문을 복사하지 않고 자동 구조화 차단 형태만 분류한다."""

    raw_values = tuple(
        dict.fromkeys(
            str(row.get(field) or "").strip()
            for field in HOURS_FIELDS
            if str(row.get(field) or "").strip()
        )
    )
    if len(raw_values) != 1:
        return "HOURS_FIELD_SHAPE_INVALID"
    raw_text = next(iter(raw_values))
    if SEASONAL_OR_VARIABLE.search(raw_text):
        return "SEASONAL_OR_VARIABLE"
    clock_range_count = len(CLOCK_RANGE.findall(raw_text))
    if clock_range_count > 1:
        return "MULTIPLE_CLOCK_RANGES"
    if clock_range_count == 1:
        rest_values = tuple(
            dict.fromkeys(
                str(row.get(field) or "").strip()
                for field in REST_FIELDS
                if str(row.get(field) or "").strip()
            )
        )
        if DAY_SCOPE.search(raw_text):
            if "매일" in raw_text:
                return "CLOCK_RANGE_DAILY_SCOPE_UNSUPPORTED"
            if "평일" in raw_text or "주말" in raw_text:
                return "CLOCK_RANGE_BROAD_DAY_SCOPE_UNSUPPORTED"
            return "CLOCK_RANGE_SPECIFIC_DAY_SCOPE_AMBIGUOUS"
        if not rest_values:
            return "CLOCK_RANGE_DAY_SCOPE_MISSING"
        if len(rest_values) > 1:
            return "CLOCK_RANGE_REST_FIELDS_CONFLICTED"
        rest_scope = next(iter(rest_values))
        if (
            ANNUAL_OPEN_SCOPE.search(rest_scope)
            or rest_scope == "주말"
            or WEEKLY_CLOSED_SCOPE.fullmatch(rest_scope)
        ):
            return "CLOCK_RANGE_SUPPORTED_SCOPE_CONFLICTED"
        if NO_CLOSURE_SCOPE_VARIANT.search(rest_scope):
            return "CLOCK_RANGE_REST_SCOPE_NO_CLOSURE_VARIANT"
        if MONTHLY_REST_SCOPE.search(rest_scope):
            return "CLOCK_RANGE_REST_SCOPE_MONTHLY"
        if HOLIDAY_REST_SCOPE.search(rest_scope):
            return "CLOCK_RANGE_REST_SCOPE_HOLIDAY"
        if IRREGULAR_REST_SCOPE.search(rest_scope):
            return "CLOCK_RANGE_REST_SCOPE_IRREGULAR"
        if "매주" in rest_scope:
            if WEEKLY_EXACT_LIST_SCOPE.fullmatch(rest_scope):
                return "CLOCK_RANGE_REST_SCOPE_WEEKLY_EXACT_LIST"
            return "CLOCK_RANGE_REST_SCOPE_WEEKLY_COMPLEX"
        return "CLOCK_RANGE_REST_SCOPE_OTHER"
    if NON_COLON_CLOCK.search(raw_text):
        return "NON_COLON_CLOCK_FORMAT"
    return "NO_EXACT_CLOCK_RANGE"


def analyze_partial_tour_intro_archive(raw_archive: bytes) -> PartialTourIntroAnalysis:
    """비밀·원문 텍스트·장소 ID를 결과에 복사하지 않고 부분 snapshot을 계수한다."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_archive))
    except zipfile.BadZipFile as error:
        raise ValueError("PARTIAL_ARCHIVE_INVALID") from error
    with archive:
        manifest = _manifest(archive)
        query_numbers: set[int] = set()
        rows: list[dict[str, Any]] = []
        for name in archive.namelist():
            if name == "manifest.json":
                continue
            matched = PAGE_NAME.fullmatch(name)
            if matched is None:
                raise ValueError("PARTIAL_ARCHIVE_MEMBER_INVALID")
            query_numbers.add(int(matched["query"]))
            rows.extend(parse_public_data_page(archive.read(name)).items)

    query_scope = int(manifest["query_count"])
    completed_queries = len(query_numbers)
    if completed_queries > query_scope:
        raise ValueError("PARTIAL_ARCHIVE_QUERY_COUNT_INVALID")
    normalized = normalize_tour_intro_opening_rules(rows)
    content_types = Counter(str(row.get("contenttypeid") or "missing") for row in rows)
    content_type_by_id = {
        str(row.get("contentid") or ""): str(row.get("contenttypeid") or "missing") for row in rows
    }
    parseable_ids = {
        record.place_fact_id.removeprefix("tourapi.place:") for record in normalized.records
    }
    parseable_content_types = Counter(
        content_type_by_id.get(content_id, "missing") for content_id in parseable_ids
    )
    rejection_reasons = Counter(item.reason_code for item in normalized.rejections)
    rejection_content_types = Counter(
        f"{content_type_by_id.get(str(item.source_record_id or ''), 'missing')}:{item.reason_code}"
        for item in normalized.rejections
    )
    unverified_ids = {
        str(item.source_record_id)
        for item in normalized.rejections
        if item.source_record_id and item.reason_code == "OPENING_HOURS_UNVERIFIED"
    }
    unverified_shapes = Counter(
        _unverified_shape(row)
        for row in rows
        if str(row.get("contentid") or "") in unverified_ids
    )
    rows_with_hours = sum(
        any(str(row.get(field) or "").strip() for field in HOURS_FIELDS) for row in rows
    )
    parseable_places = len({record.place_fact_id for record in normalized.records})
    return PartialTourIntroAnalysis(
        status="staging_partial_only",
        source_id="tourapi.place-intro",
        query_scope=query_scope,
        completed_queries=completed_queries,
        missing_queries=query_scope - completed_queries,
        completion_ratio=completed_queries / query_scope,
        raw_rows=len(rows),
        rows_with_hours=rows_with_hours,
        parseable_places=parseable_places,
        normalized_rules=len(normalized.records),
        rejected_rows=len(normalized.rejections),
        content_type_counts=dict(sorted(content_types.items())),
        parseable_place_content_type_counts=dict(sorted(parseable_content_types.items())),
        rejection_reason_counts=dict(sorted(rejection_reasons.items())),
        rejection_content_type_reason_counts=dict(sorted(rejection_content_types.items())),
        unverified_shape_counts=dict(sorted(unverified_shapes.items())),
    )
