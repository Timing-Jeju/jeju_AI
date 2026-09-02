"""커밋된 v0.7 예시 검증 테스트."""

from __future__ import annotations

import json
from pathlib import Path

from jeju_trip.domain.models import DayTripResponse

ROOT = Path(__file__).resolve().parents[2]


def test_committed_example_validates_against_response_model() -> None:
    """커밋된 예시 JSON은 v0.7 Pydantic 응답 계약을 통과해야 한다."""

    payload = json.loads((ROOT / "docs/examples/v0.7/synthetic/recommend.output.json").read_text())
    response = DayTripResponse.model_validate(payload)
    assert response.schema_version == "0.7.0"
