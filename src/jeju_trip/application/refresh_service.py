"""공공데이터 raw-first 수집을 VALIDATED acquisition까지 조정한다."""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx

from jeju_trip.infrastructure.normalization_spool import NormalizationSpool
from jeju_trip.infrastructure.public_data_http import (
    PaginationResult,
    PaginationStatus,
    PublicDataHttpError,
    PublicDataPage,
    PublicDataPayloadInvalid,
    collect_paginated,
    parse_public_data_page,
    parse_public_data_xml_page,
)
from jeju_trip.infrastructure.raw_page_spool import RawPageSpool
from jeju_trip.infrastructure.source_catalog import TravelSourceContract


@dataclass(frozen=True)
class RefreshOutcome:
    source_id: str
    status: Literal["INCOMPLETE", "QUALITY_FAILED", "VALIDATED", "STAGED", "PUBLISHED", "NO_CHANGE"]
    raw_row_count: int
    accepted_row_count: int = 0
    rejected_row_count: int = 0
    normalized_checksum: str | None = None
    reason_code: str | None = None
    publication_id: str | None = None
    dataset_version: str | None = None
    query_count: int = 0
    completed_query_count: int = 0
    acquisition_id: str | None = None


_RAW_PAGE_NAME = re.compile(
    r"^query-(?P<query>\d{6})-page-(?P<page>\d{6})"
    r"(?:-attempt-(?P<attempt>\d{6}))?\.(?P<extension>json|xml)$"
)


@dataclass(frozen=True)
class _ResumedRawBatch:
    members: tuple[tuple[int, int, int, bytes], ...]
    completed_pages: dict[int, tuple[PublicDataPage, ...]]
    maximum_attempts: dict[tuple[int, int], int]


def _safe_manifest_queries(queries: tuple[dict[str, str], ...]) -> tuple[dict[str, str], ...]:
    secret_markers = ("key", "token", "secret", "authorization", "password")
    return tuple(
        {
            key: value
            for key, value in sorted(query.items())
            if not any(marker in key.casefold() for marker in secret_markers)
        }
        for query in queries
    )


def _load_resumed_raw_batch(
    raw_archive: bytes,
    *,
    source_id: str,
    source_format: str,
    queries: tuple[dict[str, str], ...],
    scope_expansion_from_rows: int | None,
) -> _ResumedRawBatch:
    """동일 조회 범위의 raw에서 완결된 query만 원문 그대로 재사용한다."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_archive))
    except zipfile.BadZipFile as error:
        raise ValueError("REFRESH_RESUME_ARCHIVE_INVALID") from error
    expected_queries = _safe_manifest_queries(queries)
    members: list[tuple[int, int, int, bytes]] = []
    maximum_attempts: dict[tuple[int, int], int] = {}
    parsed_pages: dict[int, dict[int, tuple[int, PublicDataPage]]] = {}
    seen_members: set[tuple[int, int, int]] = set()
    with archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("REFRESH_RESUME_MANIFEST_INVALID") from error
        if not isinstance(manifest, dict):
            raise ValueError("REFRESH_RESUME_MANIFEST_INVALID")
        if (
            manifest.get("schema_version") != "1"
            or manifest.get("source_id") != source_id
            or manifest.get("query_count") != len(queries)
            or manifest.get("queries") != list(expected_queries)
            or manifest.get("scope_expansion_from_rows") != scope_expansion_from_rows
        ):
            raise ValueError("REFRESH_RESUME_SCOPE_MISMATCH")
        for name in archive.namelist():
            if name == "manifest.json":
                continue
            matched = _RAW_PAGE_NAME.fullmatch(name)
            if matched is None or matched["extension"] != "json":
                raise ValueError("REFRESH_RESUME_MEMBER_INVALID")
            query_number = int(matched["query"])
            page_number = int(matched["page"])
            attempt_number = int(matched["attempt"] or "1")
            member_key = (query_number, page_number, attempt_number)
            if (
                query_number < 1
                or query_number > len(queries)
                or page_number < 1
                or attempt_number < 1
                or member_key in seen_members
            ):
                raise ValueError("REFRESH_RESUME_MEMBER_INVALID")
            seen_members.add(member_key)
            raw_bytes = archive.read(name)
            members.append((query_number, page_number, attempt_number, raw_bytes))
            page_key = (query_number, page_number)
            maximum_attempts[page_key] = max(maximum_attempts.get(page_key, 0), attempt_number)
            try:
                page = (
                    parse_public_data_page(raw_bytes)
                    if source_format == "JSON"
                    else parse_public_data_xml_page(raw_bytes)
                    if source_format == "XML"
                    else None
                )
            except PublicDataPayloadInvalid:
                continue
            if page is None:
                raise ValueError("REFRESH_RESUME_FORMAT_UNSUPPORTED")
            if page.page_number != page_number:
                continue
            previous = parsed_pages.setdefault(query_number, {}).get(page_number)
            if previous is None or attempt_number > previous[0]:
                parsed_pages[query_number][page_number] = (attempt_number, page)

    completed_pages: dict[int, tuple[PublicDataPage, ...]] = {}
    for query_number, candidates in parsed_pages.items():
        first_value = candidates.get(1)
        if first_value is None:
            continue
        first = first_value[1]
        expected_page_count = max(1, math.ceil(first.total_count / first.rows_per_page))
        pages = tuple(
            candidates[page_number][1]
            for page_number in range(1, expected_page_count + 1)
            if page_number in candidates
        )
        if len(pages) == expected_page_count and all(
            page.total_count == first.total_count for page in pages
        ):
            completed_pages[query_number] = pages
    return _ResumedRawBatch(tuple(members), completed_pages, maximum_attempts)


class PublicDataRefreshService:
    def __init__(self, page_client: Any, raw_store: Any, source_admin: Any) -> None:
        self._page_client = page_client
        self._raw_store = raw_store
        self._source_admin = source_admin

    def refresh(
        self,
        contract: TravelSourceContract,
        endpoint: str,
        base_query: dict[str, str],
        environment: dict[str, str],
        normalizer: Any,
        rows_per_page: int = 1000,
        publish_validated: Callable[[Any, tuple[Any, ...]], Any] | None = None,
        *,
        scope_expansion_from_rows: int | None = None,
    ) -> RefreshOutcome:
        return self.refresh_batch(
            contract,
            endpoint,
            (base_query,),
            environment,
            normalizer,
            rows_per_page,
            publish_validated,
            scope_expansion_from_rows=scope_expansion_from_rows,
        )

    def refresh_batch(
        self,
        contract: TravelSourceContract,
        endpoint: str,
        queries: tuple[dict[str, str], ...],
        environment: dict[str, str],
        normalizer: Any,
        rows_per_page: int = 1000,
        publish_validated: Callable[[Any, tuple[Any, ...]], Any] | None = None,
        *,
        incomplete_reason_code: Literal["STAGING_SUPPLEMENTAL_QUERY"] | None = None,
        scope_expansion_from_rows: int | None = None,
        resume_raw_archive: bytes | None = None,
        resume_observed_at: datetime | None = None,
        stop_on_incomplete: bool = False,
    ) -> RefreshOutcome:
        """부분 API 조회들을 한 raw acquisition과 완전 snapshot으로 합쳐 발행한다."""

        if not queries:
            raise ValueError("REFRESH_BATCH_EMPTY")
        if (resume_raw_archive is None) != (resume_observed_at is None):
            raise ValueError("REFRESH_RESUME_ARGUMENTS_INCOMPLETE")
        if resume_observed_at is not None:
            if resume_observed_at.tzinfo is None:
                raise ValueError("REFRESH_RESUME_OBSERVED_AT_INVALID")
            if datetime.now(UTC) - resume_observed_at.astimezone(UTC) > timedelta(
                days=contract.temporal.freshness_days
            ):
                raise ValueError("REFRESH_RESUME_SNAPSHOT_STALE")

        manifest_queries = _safe_manifest_queries(queries)
        resumed = (
            _load_resumed_raw_batch(
                resume_raw_archive,
                source_id=contract.id,
                source_format=contract.acquisition.format,
                queries=queries,
                scope_expansion_from_rows=scope_expansion_from_rows,
            )
            if resume_raw_archive is not None
            else _ResumedRawBatch((), {}, {})
        )

        with RawPageSpool() as raw_spool:
            raw_spool.add_manifest(
                {
                    "schema_version": "1",
                    "source_id": contract.id,
                    "query_count": len(queries),
                    "queries": manifest_queries,
                    "scope_expansion_from_rows": scope_expansion_from_rows,
                }
            )
            maximum_attempts = dict(resumed.maximum_attempts)
            for query_number, page_number, attempt_number, raw_bytes in resumed.members:
                raw_spool.add_scoped_raw_page(
                    query_number,
                    page_number,
                    raw_bytes,
                    attempt_number=attempt_number,
                )
            paginations: list[PaginationResult] = []
            for query_number, base_query in enumerate(queries, start=1):
                resumed_pages = resumed.completed_pages.get(query_number)
                if resumed_pages is not None:
                    paginations.append(PaginationResult(PaginationStatus.COMPLETE, resumed_pages))
                    continue

                def fetch_page(
                    page_number: int,
                    query_values=base_query,
                    scoped_query_number=query_number,
                ):
                    query = {
                        **query_values,
                        "pageNo": str(page_number),
                        "numOfRows": str(rows_per_page),
                    }
                    for attempt in range(3):
                        attempt_number = (
                            maximum_attempts.get((scoped_query_number, page_number), 0) + 1
                        )
                        try:
                            if contract.acquisition.format == "JSON":
                                fetched = self._page_client.fetch_json(
                                    contract, endpoint, query, environment
                                )
                                raw_spool.add_scoped_raw_page(
                                    scoped_query_number,
                                    page_number,
                                    fetched.raw_bytes,
                                    attempt_number=attempt_number,
                                )
                                maximum_attempts[(scoped_query_number, page_number)] = (
                                    attempt_number
                                )
                                return parse_public_data_page(fetched.raw_bytes)
                            if contract.acquisition.format == "XML":
                                fetched = self._page_client.fetch_xml(
                                    contract, endpoint, query, environment
                                )
                                raw_spool.add_scoped_raw_page(
                                    scoped_query_number,
                                    page_number,
                                    fetched.raw_bytes,
                                    attempt_number=attempt_number,
                                )
                                maximum_attempts[(scoped_query_number, page_number)] = (
                                    attempt_number
                                )
                                return parse_public_data_xml_page(fetched.raw_bytes)
                        except PublicDataHttpError as error:
                            if str(error) == "HTTP_STATUS_429" or attempt == 2:
                                raise
                        except (PublicDataPayloadInvalid, httpx.TimeoutException):
                            if attempt == 2:
                                raise
                    raise ValueError(f"PAGINATED_FORMAT_UNSUPPORTED:{contract.acquisition.format}")

                pagination = collect_paginated(
                    fetch_page,
                    lambda _: None,
                )
                paginations.append(pagination)
                if stop_on_incomplete and pagination.status == PaginationStatus.INCOMPLETE:
                    break

            pages = tuple(page for pagination in paginations for page in pagination.pages)
            raw_row_count = sum(len(page.items) for page in pages)
            completed_query_count = sum(
                item.status == PaginationStatus.COMPLETE for item in paginations
            )
            initial_status = (
                "ACQUIRED"
                if incomplete_reason_code is None
                and len(paginations) == len(queries)
                and all(item.status == PaginationStatus.COMPLETE for item in paginations)
                else "INCOMPLETE"
            )
            registrations: list[Any] = []

            def register(pointer: Any) -> None:
                registrations.append(
                    self._source_admin.register_verified_raw(
                        contract,
                        pointer,
                        temporal_basis=contract.temporal.basis,
                        source_date=None,
                        observed_at=resume_observed_at or datetime.now(UTC),
                        raw_row_count=raw_row_count,
                        initial_status=initial_status,
                    )
                )

            self._raw_store.store_verified(
                contract,
                raw_spool.iter_chunks(),
                "zip",
                "application/zip",
                register,
            )
            if not registrations:
                raise RuntimeError("ACQUISITION_CALLBACK_NOT_CALLED")
            registration = registrations[0]
            if not pages:
                return RefreshOutcome(
                    contract.id,
                    "INCOMPLETE",
                    0,
                    reason_code=next(
                        (item.reason_code for item in paginations if item.reason_code),
                        "PUBLIC_DATA_NO_PAGES",
                    ),
                    query_count=len(queries),
                    completed_query_count=completed_query_count,
                    acquisition_id=str(registration.acquisition_id),
                )
            if initial_status == "INCOMPLETE":
                return RefreshOutcome(
                    contract.id,
                    "INCOMPLETE",
                    raw_row_count,
                    reason_code=incomplete_reason_code
                    or next(
                        (item.reason_code for item in paginations if item.reason_code),
                        "PUBLIC_DATA_BATCH_INCOMPLETE",
                    ),
                    query_count=len(queries),
                    completed_query_count=completed_query_count,
                    acquisition_id=str(registration.acquisition_id),
                )
            if not registration.created and registration.status in {
                "STAGED",
                "PUBLISHED",
                "NO_CHANGE",
            }:
                return RefreshOutcome(
                    contract.id,
                    "NO_CHANGE",
                    raw_row_count,
                    query_count=len(queries),
                    completed_query_count=completed_query_count,
                    acquisition_id=str(registration.acquisition_id),
                )
            resume_validated_publication = (
                not registration.created
                and registration.status == "VALIDATED"
                and publish_validated is not None
            )
            if (
                not registration.created
                and registration.status == "VALIDATED"
                and publish_validated is None
            ):
                return RefreshOutcome(
                    contract.id,
                    "NO_CHANGE",
                    raw_row_count,
                    query_count=len(queries),
                    completed_query_count=completed_query_count,
                    acquisition_id=str(registration.acquisition_id),
                )

            rows = [item for page in pages for item in page.items]
            normalized = normalizer(rows)
            accepted_count = len(normalized.records)
            rejected_count = len(normalized.rejections)
            total_count = accepted_count + rejected_count
            rejected_ratio = rejected_count / total_count if total_count else 1.0
            positioned_count = sum(
                1
                for record in normalized.records
                if getattr(record, "position", None) is not None
                or getattr(record, "geocoded_position", None) is not None
            )
            coordinate_ratio = positioned_count / total_count if total_count else 0.0
            quality = contract.quality
            previous_count_lookup = getattr(
                self._source_admin, "previous_published_row_count", None
            )
            previous_count = (
                previous_count_lookup(contract.id) if previous_count_lookup is not None else None
            )
            scope_expansion_baseline_mismatch = (
                scope_expansion_from_rows is not None
                and previous_count != scope_expansion_from_rows
            )
            reviewed_scope_expansion = (
                scope_expansion_from_rows is not None
                and previous_count == scope_expansion_from_rows
                and raw_row_count > scope_expansion_from_rows
            )
            row_change_ratio = (
                abs(raw_row_count - previous_count) / previous_count
                if previous_count is not None and previous_count > 0
                else 0.0
            )
            row_change_exceeded = (
                row_change_ratio > quality.maximum_row_change_ratio and not reviewed_scope_expansion
            )
            normalized_records_empty = accepted_count == 0
            quality_failed = (
                normalized_records_empty
                or raw_row_count < quality.minimum_rows
                or raw_row_count > quality.maximum_rows
                or rejected_ratio > quality.maximum_rejected_ratio
                or coordinate_ratio < quality.minimum_coordinate_ratio
                or scope_expansion_baseline_mismatch
                or row_change_exceeded
            )
            if quality_failed:
                self._source_admin.mark_quality_failed(
                    registration.acquisition_id,
                    accepted_count,
                    rejected_count,
                )
                return RefreshOutcome(
                    contract.id,
                    "QUALITY_FAILED",
                    raw_row_count,
                    accepted_count,
                    rejected_count,
                    reason_code=(
                        "NORMALIZED_RECORDS_EMPTY"
                        if normalized_records_empty
                        else (
                            "SOURCE_SCOPE_EXPANSION_BASELINE_MISMATCH"
                            if scope_expansion_baseline_mismatch
                            else (
                                "SOURCE_ROW_CHANGE_RATIO_EXCEEDED"
                                if row_change_exceeded
                                else "SOURCE_QUALITY_THRESHOLD_FAILED"
                            )
                        )
                    ),
                    query_count=len(queries),
                    completed_query_count=completed_query_count,
                    acquisition_id=str(registration.acquisition_id),
                )

            with NormalizationSpool() as normalized_spool:
                spool_result = normalized_spool.write(
                    (record.model_dump(mode="json") for record in normalized.records),
                    normalized.rejections,
                )
                if not resume_validated_publication:
                    self._source_admin.mark_validated(
                        registration.acquisition_id,
                        spool_result.accepted_count,
                        spool_result.rejected_count,
                        spool_result.normalized_checksum,
                    )
                if publish_validated is not None:
                    publication = publish_validated(registration.acquisition_id, normalized.records)
                    return RefreshOutcome(
                        contract.id,
                        "STAGED" if getattr(publication, "created", True) else "NO_CHANGE",
                        raw_row_count,
                        spool_result.accepted_count,
                        spool_result.rejected_count,
                        spool_result.normalized_checksum,
                        publication_id=str(publication.publication_id),
                        dataset_version=str(publication.dataset_version),
                        query_count=len(queries),
                        completed_query_count=completed_query_count,
                        acquisition_id=str(registration.acquisition_id),
                    )
                return RefreshOutcome(
                    contract.id,
                    "VALIDATED",
                    raw_row_count,
                    spool_result.accepted_count,
                    spool_result.rejected_count,
                    spool_result.normalized_checksum,
                    query_count=len(queries),
                    completed_query_count=completed_query_count,
                    acquisition_id=str(registration.acquisition_id),
                )
