"""v0.6 합성 예시의 결정론적 생성 결과 drift 검사."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "docs/examples/v0.6/synthetic"


def test_v06_synthetic_examples_match_fresh_generation(tmp_path: Path) -> None:
    """현재 합성 예시는 새 프로세스에서 다시 생성해도 byte 단위로 같아야 한다."""

    generated = tmp_path / "synthetic"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate_v06_synthetic_examples.py"),
            "--output",
            str(generated),
        ],
        cwd=ROOT,
        check=True,
    )

    committed_files = {path.name for path in COMMITTED.glob("*.json")}
    generated_files = {path.name for path in generated.glob("*.json")}
    assert generated_files == committed_files
    assert all(
        (generated / filename).read_bytes() == (COMMITTED / filename).read_bytes()
        for filename in committed_files
    )
