#!/bin/sh
set -eu

SERVICE_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$SERVICE_DIR"
UV=${UV_BIN:-uv}

command -v "$UV" >/dev/null 2>&1 || {
  echo "uv가 필요합니다. README의 개발 환경 준비 절차를 먼저 실행하세요." >&2
  exit 1
}

echo "[FastAPI 품질 게이트] 잠금 파일 기반 의존성 동기화"
"$UV" sync --locked --group dev

echo "[FastAPI 품질 게이트] Ruff 린트와 포맷 검사"
"$UV" run --frozen ruff check .
"$UV" run --frozen ruff format --check .

PRODUCTION_FILES=$(find . -type f -name '*.py' \
  ! -path './.venv/*' \
  ! -name 'test_*.py' \
  ! -name '*_test.py' \
  ! -name 'conftest.py' -print)
TEST_FILES=$(find . -type f \( -name 'test_*.py' -o -name '*_test.py' \) \
  ! -path './.venv/*' -print)

if [ -z "$PRODUCTION_FILES" ]; then
  echo "[FastAPI 품질 게이트] 구현 파일이 없어 mypy와 pytest를 생략합니다."
  exit 0
fi

if [ -z "$TEST_FILES" ]; then
  echo "운영 Python 파일이 있지만 대응 테스트가 없습니다." >&2
  exit 1
fi

echo "[FastAPI 품질 게이트] mypy 타입 검사"
find . -type f -name '*.py' ! -path './.venv/*' -print0 \
  | xargs -0 "$UV" run --frozen mypy

echo "[FastAPI 품질 게이트] pytest"
"$UV" run --frozen pytest
