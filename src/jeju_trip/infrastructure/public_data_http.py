"""공공데이터 API의 bounded JSON/XML 수집과 중단 안전 pagination."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import unquote

import httpx

from jeju_trip.infrastructure.preflight import SourcePreflight
from jeju_trip.infrastructure.source_catalog import TravelSourceContract


class SourcePreflightFailed(RuntimeError):
    """source readiness가 네트워크 전에 실패한 경우."""


class UnexpectedRedirect(RuntimeError):
    """redirect 금지 계약을 API가 위반한 경우."""


class ResponseTooLarge(RuntimeError):
    """응답이 source contract의 최대 byte를 넘은 경우."""


class PublicDataHttpError(RuntimeError):
    """본문을 노출하지 않는 안전한 HTTP 오류."""


class PublicDataPayloadInvalid(ValueError):
    """공공데이터 envelope가 계약과 다른 경우."""


@dataclass(frozen=True)
class FetchedJson:
    raw_bytes: bytes
    document: dict[str, Any]
    retrieved_at: datetime


@dataclass(frozen=True)
class FetchedXml:
    raw_bytes: bytes
    root: ET.Element
    retrieved_at: datetime


class BoundedPublicDataClient:
    def __init__(self, client: httpx.Client, preflight: SourcePreflight | None = None) -> None:
        self._client = client
        self._preflight = preflight or SourcePreflight()

    def fetch_json(
        self,
        contract: TravelSourceContract,
        endpoint: str,
        query: dict[str, str],
        environment: dict[str, str],
    ) -> FetchedJson:
        raw_bytes, retrieved_at = self._fetch(
            contract, endpoint, query, environment, response_type="json"
        )
        try:
            document = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PublicDataPayloadInvalid("PUBLIC_DATA_JSON_INVALID") from error
        if not isinstance(document, dict):
            raise PublicDataPayloadInvalid("PUBLIC_DATA_ROOT_NOT_OBJECT")
        return FetchedJson(raw_bytes, document, retrieved_at)

    def fetch_xml(
        self,
        contract: TravelSourceContract,
        endpoint: str,
        query: dict[str, str],
        environment: dict[str, str],
    ) -> FetchedXml:
        raw_bytes, retrieved_at = self._fetch(
            contract, endpoint, query, environment, response_type=None
        )
        try:
            root = ET.fromstring(raw_bytes)
        except ET.ParseError as error:
            raise PublicDataPayloadInvalid("PUBLIC_DATA_XML_INVALID") from error
        return FetchedXml(raw_bytes, root, retrieved_at)

    def _fetch(
        self,
        contract: TravelSourceContract,
        endpoint: str,
        query: dict[str, str],
        environment: dict[str, str],
        *,
        response_type: str | None,
    ) -> tuple[bytes, datetime]:
        readiness = self._preflight.check(contract, environment)
        if readiness.status != "PASS":
            raise SourcePreflightFailed("|".join(readiness.reason_codes))

        url = f"{contract.acquisition.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        contract.assert_network_request_allowed(url)
        parameters = dict(query)
        if readiness.permitted_secret_names:
            if len(readiness.permitted_secret_names) != 1:
                raise SourcePreflightFailed("SOURCE_SECRET_MAPPING_AMBIGUOUS")
            secret_name = readiness.permitted_secret_names[0]
            parameters["serviceKey"] = unquote(environment[secret_name])
        if response_type is not None:
            parameters.setdefault("_type", response_type)

        with self._client.stream("GET", url, params=parameters, follow_redirects=False) as response:
            if response.is_redirect:
                raise UnexpectedRedirect("HTTP_REDIRECT_REJECTED")
            if response.status_code != 200:
                raise PublicDataHttpError(f"HTTP_STATUS_{response.status_code}")
            declared_length = response.headers.get("content-length")
            if (
                declared_length
                and int(declared_length) > contract.acquisition.maximum_response_bytes
            ):
                raise ResponseTooLarge("HTTP_RESPONSE_TOO_LARGE")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > contract.acquisition.maximum_response_bytes:
                    raise ResponseTooLarge("HTTP_RESPONSE_TOO_LARGE")

        return bytes(content), datetime.now(UTC)


@dataclass(frozen=True)
class PublicDataPage:
    page_number: int
    rows_per_page: int
    total_count: int
    items: tuple[dict[str, Any], ...]
    raw_bytes: bytes


class PaginationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class PaginationResult:
    status: PaginationStatus
    pages: tuple[PublicDataPage, ...]
    reason_code: str | None = None


def parse_public_data_page(raw_bytes: bytes) -> PublicDataPage:
    try:
        root = json.loads(raw_bytes)
        if not isinstance(root, dict):
            raise PublicDataPayloadInvalid("PUBLIC_DATA_ROOT_NOT_OBJECT")
        response = root.get("response", root)
        header = response["header"]
        body = response["body"]
        result_code = str(header["resultCode"])
        if result_code not in {"00", "0000"}:
            safe_code = (
                result_code if 1 <= len(result_code) <= 16 and result_code.isalnum() else "UNKNOWN"
            )
            raise PublicDataPayloadInvalid(f"PUBLIC_DATA_RESULT_{safe_code}")
        raw_items = body.get("items", {})
        if raw_items in (None, ""):
            items: list[dict[str, Any]] = []
        else:
            item_value = raw_items.get("item", [])
            if isinstance(item_value, dict):
                items = [item_value]
            elif isinstance(item_value, list) and all(
                isinstance(item, dict) for item in item_value
            ):
                items = item_value
            else:
                raise PublicDataPayloadInvalid("PUBLIC_DATA_ITEMS_INVALID")
        page_number = int(body["pageNo"])
        rows_per_page = int(body["numOfRows"])
        total_count = int(body["totalCount"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, PublicDataPayloadInvalid):
            raise
        raise PublicDataPayloadInvalid("PUBLIC_DATA_ENVELOPE_INVALID") from error
    if rows_per_page == 0 and total_count == 0:
        rows_per_page = 1
    if page_number < 1 or rows_per_page < 1 or total_count < 0:
        raise PublicDataPayloadInvalid("PUBLIC_DATA_PAGINATION_INVALID")
    return PublicDataPage(
        page_number=page_number,
        rows_per_page=rows_per_page,
        total_count=total_count,
        items=tuple(items),
        raw_bytes=raw_bytes,
    )


def parse_public_data_xml_page(raw_bytes: bytes) -> PublicDataPage:
    """data.go.kr의 공통 XML envelope를 JSON page와 같은 형태로 정규화한다."""

    try:
        root = ET.fromstring(raw_bytes)
        result_code = (root.findtext("./header/resultCode") or "").strip()
        if result_code not in {"00", "0000"}:
            raise PublicDataPayloadInvalid(f"PUBLIC_DATA_RESULT_{result_code or 'MISSING'}")
        body = root.find("./body")
        if body is None:
            raise PublicDataPayloadInvalid("PUBLIC_DATA_ENVELOPE_INVALID")
        items = tuple(
            {child.tag: child.text or "" for child in item} for item in body.findall("./items/item")
        )
        page_number = int(body.findtext("pageNo") or "")
        rows_per_page = int(body.findtext("numOfRows") or "")
        total_count = int(body.findtext("totalCount") or "")
    except (ET.ParseError, TypeError, ValueError) as error:
        raise PublicDataPayloadInvalid("PUBLIC_DATA_ENVELOPE_INVALID") from error
    if rows_per_page == 0 and total_count == 0:
        rows_per_page = 1
    if page_number < 1 or rows_per_page < 1 or total_count < 0:
        raise PublicDataPayloadInvalid("PUBLIC_DATA_PAGINATION_INVALID")
    return PublicDataPage(
        page_number=page_number,
        rows_per_page=rows_per_page,
        total_count=total_count,
        items=items,
        raw_bytes=raw_bytes,
    )


def collect_paginated(
    fetch_page: Callable[[int], PublicDataPage],
    on_complete: Callable[[tuple[PublicDataPage, ...]], object],
    archive_page: Callable[[PublicDataPage], object] | None = None,
    maximum_pages: int = 10_000,
) -> PaginationResult:
    pages: list[PublicDataPage] = []
    try:
        first = fetch_page(1)
        pages.append(first)
        if archive_page is not None:
            archive_page(first)
        expected_pages = max(1, math.ceil(first.total_count / first.rows_per_page))
        if expected_pages > maximum_pages:
            return PaginationResult(
                PaginationStatus.INCOMPLETE,
                tuple(pages),
                "PAGINATION_LIMIT_EXCEEDED",
            )
        for page_number in range(2, expected_pages + 1):
            page = fetch_page(page_number)
            if page.page_number != page_number or page.total_count != first.total_count:
                return PaginationResult(
                    PaginationStatus.INCOMPLETE,
                    tuple(pages),
                    "PAGINATION_METADATA_CHANGED",
                )
            pages.append(page)
            if archive_page is not None:
                archive_page(page)
    except PublicDataPayloadInvalid as error:
        reason_code = str(error)
        if not reason_code.startswith("PUBLIC_DATA_") or len(reason_code) > 64:
            reason_code = "PUBLIC_DATA_PAYLOAD_INVALID"
        return PaginationResult(
            PaginationStatus.INCOMPLETE,
            tuple(pages),
            reason_code,
        )
    except PublicDataHttpError as error:
        reason_code = str(error)
        if re.fullmatch(r"HTTP_STATUS_[1-5][0-9]{2}", reason_code) is None:
            reason_code = "PUBLIC_DATA_HTTP_ERROR"
        return PaginationResult(
            PaginationStatus.INCOMPLETE,
            tuple(pages),
            reason_code,
        )
    except Exception:
        return PaginationResult(
            PaginationStatus.INCOMPLETE,
            tuple(pages),
            "PAGINATION_INTERRUPTED",
        )
    completed_pages = tuple(pages)
    on_complete(completed_pages)
    return PaginationResult(PaginationStatus.COMPLETE, completed_pages)
