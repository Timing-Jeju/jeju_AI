"""공공 API HTTP 경계와 pagination 안전성 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jeju_trip.infrastructure.public_data_http import (
    BoundedPublicDataClient,
    PaginationStatus,
    PublicDataHttpError,
    PublicDataPayloadInvalid,
    ResponseTooLarge,
    SourcePreflightFailed,
    UnexpectedRedirect,
    collect_paginated,
    parse_public_data_page,
    parse_public_data_xml_page,
)
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[2]


def _response_payload(page: int, total: int, items: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "OK"},
                "body": {
                    "pageNo": page,
                    "numOfRows": 1,
                    "totalCount": total,
                    "items": {"item": items},
                },
            }
        }
    ).encode()


def test_missing_secret_stops_before_http_transport() -> None:
    """필수 API key가 없으면 실제 HTTP transport를 호출하기 전에 중단해야 한다."""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"{}")

    source = next(
        source
        for source in SourceCatalog.load(ROOT / "config/data_sources.toml").sources
        if source.id == "holiday.special-day"
    )
    client = BoundedPublicDataClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(SourcePreflightFailed, match="SOURCE_SECRET_MISSING"):
        client.fetch_xml(source, "getRestDeInfo", {}, {})
    assert calls == 0


def test_redirect_response_is_rejected_without_following_location() -> None:
    """공공 API redirect는 다른 host로 따라가지 않고 안전하게 거부해야 한다."""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://evil.example/raw"})

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    client = BoundedPublicDataClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(UnexpectedRedirect, match="HTTP_REDIRECT_REJECTED"):
        client.fetch_json(
            source,
            "getCrdntPrxmtSttnList",
            {"gpsLati": "33.5", "gpsLong": "126.5"},
            {"JEJU_TAGO_SERVICE_KEY": "secret-key"},
        )
    assert calls == 1


def test_response_larger_than_contract_limit_is_rejected_safely() -> None:
    """계약 최대 크기를 넘는 응답은 원문이나 API key 노출 없이 거부해야 한다."""

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("tago.bus-stop")
    small_acquisition = source.acquisition.model_copy(update={"maximum_response_bytes": 10})
    source = source.model_copy(update={"acquisition": small_acquisition})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"private":"body"}')

    client = BoundedPublicDataClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ResponseTooLarge) as captured:
        client.fetch_json(
            source,
            "getCrdntPrxmtSttnList",
            {},
            {"JEJU_TAGO_SERVICE_KEY": "secret-key"},
        )
    assert "private" not in str(captured.value)
    assert "secret-key" not in str(captured.value)


def test_interrupted_pagination_keeps_pages_but_never_publishes() -> None:
    """중단된 pagination 원본 페이지는 보존하되 publication callback을 호출하지 않아야 한다."""

    published: list[object] = []

    def fetch_page(page_number: int):
        if page_number == 2:
            raise TimeoutError("timeout")
        return parse_public_data_page(_response_payload(1, 2, [{"nodeid": "1"}]))

    result = collect_paginated(fetch_page, published.append)
    assert result.status == PaginationStatus.INCOMPLETE
    assert len(result.pages) == 1
    assert result.reason_code == "PAGINATION_INTERRUPTED"
    assert published == []


def test_pagination_preserves_safe_public_data_result_code() -> None:
    """공공 API result code 오류는 원문 없이 안전한 구조화 중단 사유로 보존해야 한다."""

    def fetch_page(page_number: int):
        raise PublicDataPayloadInvalid("PUBLIC_DATA_RESULT_22")

    result = collect_paginated(fetch_page, lambda pages: None)

    assert result.status == PaginationStatus.INCOMPLETE
    assert result.pages == ()
    assert result.reason_code == "PUBLIC_DATA_RESULT_22"


def test_pagination_preserves_safe_http_status_without_response_body() -> None:
    """재개 판단에 필요한 HTTP 상태는 원문 없이 구조화된 중단 사유로 보존해야 한다."""

    def fetch_page(page_number: int):
        raise PublicDataHttpError("HTTP_STATUS_429")

    result = collect_paginated(fetch_page, lambda pages: None)

    assert result.status == PaginationStatus.INCOMPLETE
    assert result.pages == ()
    assert result.reason_code == "HTTP_STATUS_429"


def test_complete_pagination_publishes_exactly_once() -> None:
    """모든 페이지가 완성된 수집만 raw-first callback에 정확히 한 번 전달해야 한다."""

    published: list[object] = []

    def fetch_page(page_number: int):
        return parse_public_data_page(
            _response_payload(page_number, 2, [{"nodeid": str(page_number)}])
        )

    result = collect_paginated(fetch_page, published.append)
    assert result.status == PaginationStatus.COMPLETE
    assert len(result.pages) == 2
    assert len(published) == 1


def test_empty_detail_result_with_zero_page_size_is_complete() -> None:
    """TourAPI 상세 0건 응답의 numOfRows=0은 누락이 아닌 완전한 빈 조회로 처리해야 한다."""

    payload = json.dumps(
        {
            "response": {
                "header": {"resultCode": "0000", "resultMsg": "OK"},
                "body": {
                    "pageNo": 1,
                    "numOfRows": 0,
                    "totalCount": 0,
                    "items": "",
                },
            }
        }
    ).encode()

    page = parse_public_data_page(payload)
    result = collect_paginated(lambda page_number: page, lambda pages: None)

    assert page.rows_per_page == 1
    assert page.items == ()
    assert result.status == PaginationStatus.COMPLETE


def test_jeju_city_gateway_envelope_without_response_wrapper_is_supported() -> None:
    """제주시 API gateway의 header·body 최상위 envelope도 안전하게 페이지로 해석해야 한다."""

    payload = json.dumps(
        {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {
                "pageNo": 1,
                "numOfRows": 100,
                "totalCount": 1,
                "items": {"item": {"dataCd": "REST-1"}},
            },
        }
    ).encode()

    page = parse_public_data_page(payload)

    assert page.total_count == 1
    assert page.items == ({"dataCd": "REST-1"},)


def test_official_xml_envelope_is_normalized_without_json_hint() -> None:
    """특일 API의 공식 XML envelope를 공통 pagination page로 변환해야 한다."""

    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <response><header><resultCode>00</resultCode></header><body>
    <items><item><dateName>\xea\xb4\x91\xeb\xb3\xb5\xec\xa0\x88</dateName><isHoliday>Y</isHoliday><locdate>20260815</locdate></item></items>
    <numOfRows>10</numOfRows><pageNo>1</pageNo><totalCount>1</totalCount>
    </body></response>"""
    page = parse_public_data_xml_page(payload)
    assert page.page_number == 1
    assert page.items == (
        {"dateName": "\uad11\ubcf5\uc808", "isHoliday": "Y", "locdate": "20260815"},
    )


def test_xml_client_does_not_force_json_response_parameter() -> None:
    """XML 계약 호출에는 JSON 응답 강제 파라미터를 추가하지 않아야 한다."""

    observed_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_query
        observed_query = request.url.query.decode()
        return httpx.Response(200, content=b"<response />")

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("holiday.special-day")
    client = BoundedPublicDataClient(httpx.Client(transport=httpx.MockTransport(handler)))
    client.fetch_xml(
        source,
        "getRestDeInfo",
        {"solYear": "2026"},
        {"JEJU_HOLIDAY_SERVICE_KEY": "secret-key"},
    )
    assert "_type=" not in observed_query
    assert "serviceKey=secret-key" in observed_query


def test_public_data_encoding_key_is_decoded_exactly_once() -> None:
    """공공데이터포털 Encoding 인증키도 이중 인코딩 없이 한 번만 복원해야 한다."""

    observed_service_key = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_service_key
        observed_service_key = request.url.params["serviceKey"]
        return httpx.Response(200, content=b"<response />")

    source = SourceCatalog.load(ROOT / "config/data_sources.toml").require("holiday.special-day")
    client = BoundedPublicDataClient(httpx.Client(transport=httpx.MockTransport(handler)))
    client.fetch_xml(
        source,
        "getRestDeInfo",
        {"solYear": "2026"},
        {"JEJU_HOLIDAY_SERVICE_KEY": "sample%2Bkey%2Fvalue%3D"},
    )

    assert observed_service_key == "sample+key/value="
