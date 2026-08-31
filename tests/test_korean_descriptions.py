"""한글 테스트 설명 검사기 자체 테스트."""

from __future__ import annotations

from pathlib import Path

from scripts.check_korean_test_descriptions import inspect_file


def test_checker_rejects_missing_korean_docstring(tmp_path: Path) -> None:
    """한글 docstring이 없는 테스트를 검사기가 실패시켜야 한다."""

    target = tmp_path / "test_bad.py"
    target.write_text('def test_bad():\n    """english only"""\n    pass\n')
    failures = inspect_file(target)
    assert len(failures) == 1
