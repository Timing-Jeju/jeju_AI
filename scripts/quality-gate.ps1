$ErrorActionPreference = "Stop"

$serviceDir = Split-Path -Parent $PSScriptRoot
Set-Location $serviceDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv가 필요합니다. README의 개발 환경 준비 절차를 먼저 실행하세요."
}

Write-Host "[FastAPI 품질 게이트] 잠금 파일 기반 의존성 동기화"
uv sync --locked --group dev
uv run --frozen ruff check .
uv run --frozen ruff format --check .

$pythonFiles = Get-ChildItem -Recurse -File -Filter *.py |
  Where-Object { $_.FullName -notmatch '[\\/]\.venv[\\/]' }
$productionFiles = $pythonFiles |
  Where-Object { $_.Name -notmatch '^(test_.*|.*_test|conftest)\.py$' }
$testFiles = $pythonFiles |
  Where-Object { $_.Name -match '^(test_.*|.*_test)\.py$' }

if (-not $productionFiles) {
  Write-Host "[FastAPI 품질 게이트] 구현 파일이 없어 mypy와 pytest를 생략합니다."
  exit 0
}
if (-not $testFiles) {
  throw "운영 Python 파일이 있지만 대응 테스트가 없습니다."
}

uv run --frozen mypy @($pythonFiles.FullName)
uv run --frozen pytest
