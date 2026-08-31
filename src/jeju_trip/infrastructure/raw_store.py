"""검증 후에만 acquisition 등록을 허용하는 private raw object 저장 흐름."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jeju_trip.infrastructure.source_catalog import TravelSourceContract


@dataclass(frozen=True)
class ObjectHead:
    checksum: str
    byte_length: int
    version_id: str


@dataclass(frozen=True)
class RawObjectPointer:
    source_id: str
    checksum: str
    bucket: str
    object_key: str
    object_version_id: str
    byte_length: int
    content_type: str


class ObjectClient(Protocol):
    def put_if_absent(
        self, bucket: str, key: str, path: Path, checksum: str, content_type: str
    ) -> None: ...

    def head(self, bucket: str, key: str) -> ObjectHead: ...


class RawObjectVerificationError(RuntimeError):
    """업로드한 객체가 로컬 checksum 또는 길이와 다를 때 발생한다."""


class RawStorageForbiddenError(ValueError):
    """source contract가 원본 저장을 허용하지 않을 때 발생한다."""


class PrivateRawObjectStore:
    """원본을 DB 밖 private object storage에 content-addressed 방식으로 저장한다."""

    bucket = "jeju-private-raw"

    def __init__(self, client: ObjectClient) -> None:
        self._client = client

    def store_verified(
        self,
        contract: TravelSourceContract,
        chunks: Iterable[bytes],
        extension: str,
        content_type: str,
        register_acquisition: Callable[[RawObjectPointer], None],
    ) -> RawObjectPointer:
        if not contract.license.raw_private_storage_allowed:
            raise RawStorageForbiddenError(f"RAW_STORAGE_FORBIDDEN:{contract.id}")
        fd, filename = tempfile.mkstemp(prefix="jeju-raw-")
        os.chmod(filename, 0o600)
        path = Path(filename)
        digest = hashlib.sha256()
        length = 0
        try:
            with os.fdopen(fd, "wb") as stream:
                for chunk in chunks:
                    stream.write(chunk)
                    digest.update(chunk)
                    length += len(chunk)
            checksum = digest.hexdigest()
            key = f"raw/v1/{contract.id}/{checksum[:2]}/{checksum}.{extension.lstrip('.')}"
            self._client.put_if_absent(self.bucket, key, path, checksum, content_type)
            head = self._client.head(self.bucket, key)
            if head.checksum != checksum or head.byte_length != length:
                raise RawObjectVerificationError("RAW_OBJECT_HEAD_MISMATCH")
            if not head.version_id.strip():
                raise RawObjectVerificationError("RAW_OBJECT_VERSION_MISSING")
            pointer = RawObjectPointer(
                source_id=contract.id,
                checksum=checksum,
                bucket=self.bucket,
                object_key=key,
                object_version_id=head.version_id,
                byte_length=length,
                content_type=content_type,
            )
            register_acquisition(pointer)
            return pointer
        finally:
            path.unlink(missing_ok=True)
