"""git metadata가 없는 작업공간용 v0.6 산출물 checksum 검증."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/manifests/v06-current.sha256"


def test_v06_checksum_manifest_matches_current_artifacts() -> None:
    """v0.6 변경 범위 파일은 고정 manifest의 SHA-256과 모두 일치해야 한다."""

    entries = [
        line.split("  ", maxsplit=1)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    paths = [path for _, path in entries]
    assert entries
    assert len(paths) == len(set(paths))
    assert all((ROOT / path).is_file() for path in paths)
    assert all(
        hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
        for expected, path in entries
    )
