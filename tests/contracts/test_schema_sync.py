"""생성 JSON Schema 동기화 테스트."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jeju_trip.domain.models import DayTripResponse, RecommendDayTripsInput

ROOT = Path(__file__).resolve().parents[2]


def test_committed_response_schema_matches_pydantic_model() -> None:
    """커밋된 응답 Schema는 Pydantic 모델에서 생성한 결과와 같아야 한다."""

    committed = json.loads(
        (ROOT / "docs/contracts/day-trip-recommendations.schema.json").read_text()
    )
    assert committed == DayTripResponse.model_json_schema()


def test_committed_input_schema_matches_pydantic_model() -> None:
    """커밋된 추천 입력 Schema는 Pydantic 모델에서 생성한 결과와 같아야 한다."""

    committed = json.loads(
        (ROOT / "docs/contracts/recommend-day-trips.input.schema.json").read_text()
    )
    assert committed == RecommendDayTripsInput.model_json_schema()


def test_all_public_schemas_match_fresh_generation(tmp_path: Path) -> None:
    """모든 공개 도구 Schema는 Pydantic 생성기를 다시 실행한 결과와 같아야 한다."""

    generated = tmp_path / "contracts"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate_json_schemas.py"),
            "--output",
            str(generated),
        ],
        cwd=ROOT,
        check=True,
    )
    committed = ROOT / "docs/contracts"
    committed_files = {path.name for path in committed.glob("*.json")}
    generated_files = {path.name for path in generated.glob("*.json")}

    assert generated_files == committed_files
    assert all(
        (generated / filename).read_bytes() == (committed / filename).read_bytes()
        for filename in committed_files
    )
