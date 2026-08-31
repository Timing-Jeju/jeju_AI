"""원본 객체 검증 순서 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from jeju_trip.infrastructure.raw_store import (
    ObjectHead,
    PrivateRawObjectStore,
    RawObjectVerificationError,
    RawStorageForbiddenError,
)
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[2]


class FakeObjectClient:
    def __init__(self, corrupt_head: bool = False, version_id: str = "version-1") -> None:
        self.corrupt_head = corrupt_head
        self.version_id = version_id
        self.checksum = ""
        self.length = 0

    def put_if_absent(
        self, bucket: str, key: str, path: Path, checksum: str, content_type: str
    ) -> None:
        self.checksum = checksum
        self.length = path.stat().st_size

    def head(self, bucket: str, key: str) -> ObjectHead:
        return ObjectHead(
            checksum="0" * 64 if self.corrupt_head else self.checksum,
            byte_length=self.length,
            version_id=self.version_id,
        )


def test_raw_verification_failure_does_not_register_acquisition() -> None:
    """원본 객체 검증이 실패하면 acquisition을 등록하지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "transport.stop-identity-map"
    )
    registered: list[object] = []
    store = PrivateRawObjectStore(FakeObjectClient(corrupt_head=True))
    with pytest.raises(RawObjectVerificationError):
        store.store_verified(
            contract,
            [b"row\n"],
            "ndjson",
            "application/x-ndjson",
            registered.append,
        )
    assert registered == []


def test_tmap_raw_body_cannot_enter_private_store() -> None:
    """TMAP 응답은 MinIO raw object 경로에 저장할 수 없어야 한다."""

    contract = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require("tmap.pedestrian")
    store = PrivateRawObjectStore(FakeObjectClient())
    with pytest.raises(RawStorageForbiddenError):
        store.store_verified(contract, [b"{}"], "json", "application/json", lambda _: None)


def test_raw_object_without_version_is_not_registered() -> None:
    """버전 ID가 없는 원본 객체는 acquisition으로 등록하지 않아야 한다."""

    contract = SourceCatalog.load(ROOT / "config" / "data_sources.toml").require(
        "transport.stop-identity-map"
    )
    registered: list[object] = []
    store = PrivateRawObjectStore(FakeObjectClient(version_id=""))

    with pytest.raises(RawObjectVerificationError, match="RAW_OBJECT_VERSION_MISSING"):
        store.store_verified(
            contract,
            [b"row\n"],
            "ndjson",
            "application/x-ndjson",
            registered.append,
        )

    assert registered == []
