"""pagination 원본 페이지를 owner-only ZIP spool에 즉시 기록한다."""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

from jeju_trip.infrastructure.public_data_http import PublicDataPage


class RawPageSpool:
    def __init__(self) -> None:
        fd, filename = tempfile.mkstemp(prefix="jeju-raw-pages-", suffix=".zip")
        os.chmod(filename, 0o600)
        os.close(fd)
        self.path = Path(filename)
        self._page_numbers: set[tuple[int, int, int]] = set()
        self._manifest_added = False

    def add_manifest(self, manifest: dict[str, object]) -> None:
        """비밀값을 제외한 조회 범위를 deterministic manifest로 보존한다."""

        if self._manifest_added:
            raise ValueError("RAW_MANIFEST_DUPLICATED")
        encoded = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        with zipfile.ZipFile(self.path, mode="a", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", encoded)
        self._manifest_added = True

    def add_page(self, page: PublicDataPage) -> None:
        self.add_scoped_page(1, page)

    def add_scoped_page(self, query_number: int, page: PublicDataPage) -> None:
        """여러 부분 조회를 값 노출 없이 하나의 원본 묶음으로 보존한다."""

        self.add_scoped_raw_page(query_number, page.page_number, page.raw_bytes)

    def add_scoped_raw_page(
        self,
        query_number: int,
        page_number: int,
        raw_bytes: bytes,
        *,
        attempt_number: int = 1,
    ) -> None:
        """파싱 성공 여부와 무관하게 응답 원문을 먼저 owner-only ZIP에 보존한다."""

        scoped_page = (query_number, page_number, attempt_number)
        if scoped_page in self._page_numbers:
            raise ValueError("RAW_PAGE_DUPLICATED")
        attempt_suffix = "" if attempt_number == 1 else f"-attempt-{attempt_number:06d}"
        with zipfile.ZipFile(self.path, mode="a", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(
                f"query-{query_number:06d}-page-{page_number:06d}{attempt_suffix}.json",
                raw_bytes,
            )
        self._page_numbers.add(scoped_page)

    def iter_chunks(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with self.path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                yield chunk

    def read_page(self, page_number: int) -> bytes:
        with zipfile.ZipFile(self.path) as archive:
            return archive.read(f"query-000001-page-{page_number:06d}.json")

    def close(self) -> None:
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> RawPageSpool:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
