"""모든 Python 테스트가 한글 목적 설명을 갖는지 검사한다."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

KOREAN = re.compile(r"[가-힣]")
WAIVER = "# korean-test-description-waiver:"


def _has_waiver(lines: list[str], line_number: int) -> bool:
    previous = lines[line_number - 2].strip() if line_number >= 2 else ""
    return previous.startswith(WAIVER) and len(previous.removeprefix(WAIVER).strip()) > 0


def inspect_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text, filename=str(path))
    failures: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_") or _has_waiver(lines, node.lineno):
            continue
        description = ast.get_docstring(node, clean=True)
        if not description or not KOREAN.search(description) or "TODO" in description.upper():
            failures.append(f"{path}:{node.lineno} {node.name}: 한글 docstring이 필요합니다")
    return failures


def main() -> int:
    failures = [
        message
        for path in sorted(Path("tests").rglob("test_*.py"))
        for message in inspect_file(path)
    ]
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("상태: Pass\n검증 근거: 모든 Python 테스트에 한글 목적 설명이 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
