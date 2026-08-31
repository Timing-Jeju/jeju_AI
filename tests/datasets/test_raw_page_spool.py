"""pagination 원본 ZIP spool 테스트."""

from __future__ import annotations

import json
import stat
import zipfile

from jeju_trip.infrastructure.public_data_http import (
    PaginationStatus,
    collect_paginated,
    parse_public_data_page,
)
from jeju_trip.infrastructure.raw_page_spool import RawPageSpool
from tests.datasets.test_public_data_http import _response_payload


def test_raw_page_spool_is_owner_only_and_preserves_exact_bytes() -> None:
    """원본 페이지 spool은 0600 권한으로 각 HTTP body의 정확한 bytes를 보존해야 한다."""

    raw = _response_payload(1, 1, [{"contentid": "1"}])
    with RawPageSpool() as spool:
        spool.add_page(parse_public_data_page(raw))
        path = spool.path
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert spool.read_page(1) == raw
        assert b"".join(spool.iter_chunks()).startswith(b"PK")
    assert not path.exists()


def test_raw_page_spool_preserves_safe_query_manifest() -> None:
    """집계 조회 원본은 비밀값 없이 조회 범위를 재현할 manifest를 포함해야 한다."""

    with RawPageSpool() as spool:
        spool.add_manifest({"source_id": "holiday.special-day", "query_count": 2})
        with zipfile.ZipFile(spool.path) as archive:
            manifest = json.loads(archive.read("manifest.json"))

    assert manifest == {"query_count": 2, "source_id": "holiday.special-day"}


def test_interrupted_pagination_archives_completed_pages_only() -> None:
    """pagination 중단 전 완료된 페이지는 spool에 남고 publication은 호출되지 않아야 한다."""

    published: list[object] = []
    raw = _response_payload(1, 2, [{"contentid": "1"}])

    def fetch_page(page_number: int):
        if page_number == 2:
            raise TimeoutError
        return parse_public_data_page(raw)

    with RawPageSpool() as spool:
        result = collect_paginated(fetch_page, published.append, spool.add_page)
        assert result.status == PaginationStatus.INCOMPLETE
        assert spool.read_page(1) == raw
        assert published == []
