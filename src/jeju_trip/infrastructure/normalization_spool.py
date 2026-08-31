"""대용량 정규화 결과를 owner-only NDJSON spool로 처리한다."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Rejection:
    source_record_id: str | None
    field_name: str | None
    reason_code: str


@dataclass(frozen=True)
class NormalizationResult:
    accepted_count: int
    rejected_count: int
    normalized_checksum: str
    spool_path: Path


class NormalizationSpool:
    """정규화된 canonical JSON만 쓰고 종료 시 임시 파일을 제거한다."""

    def __init__(self) -> None:
        fd, filename = tempfile.mkstemp(prefix="jeju-normalized-", suffix=".ndjson")
        os.chmod(filename, 0o600)
        os.close(fd)
        self.path = Path(filename)

    def write(
        self,
        accepted: Iterable[dict[str, Any]],
        rejections: Iterable[Rejection] = (),
    ) -> NormalizationResult:
        digest = hashlib.sha256()
        accepted_count = 0
        with self.path.open("wb") as stream:
            for row in accepted:
                canonical = json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
                stream.write(canonical + b"\n")
                digest.update(canonical)
                digest.update(b"\n")
                accepted_count += 1
        rejection_list = tuple(rejections)
        return NormalizationResult(
            accepted_count=accepted_count,
            rejected_count=len(rejection_list),
            normalized_checksum=digest.hexdigest(),
            spool_path=self.path,
        )

    def rows(self) -> Iterator[dict[str, Any]]:
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)

    def close(self) -> None:
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> NormalizationSpool:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
