"""LLM 출력 grounding 경계 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jeju_trip.agents.grounding import (
    ModelCandidateOrders,
    ModelReason,
    merge_structured_over_original,
    validate_model_reasons,
)


def test_model_reason_rejects_unknown_fact_id() -> None:
    """모델이 미지의 evidence fact ID를 만든 추천 이유를 거부해야 한다."""

    with pytest.raises(ValueError, match="UNKNOWN_EVIDENCE"):
        validate_model_reasons((ModelReason("좋은 일정", ("fact-new",)),), {"fact-known"})


def test_structured_input_wins_over_original_text_parse() -> None:
    """구조화 입력과 원문 해석이 충돌하면 구조화 입력을 우선해야 한다."""

    merged = merge_structured_over_original(
        {"total_budget_krw": None}, {"total_budget_krw": 30000, "pace": "relaxed"}
    )
    assert merged == {"total_budget_krw": None, "pace": "relaxed"}


def test_model_order_contract_rejects_numeric_route_claims() -> None:
    """LLM 후보 순서 출력에 시간·거리·비용 숫자 필드가 있으면 계약 위반이어야 한다."""

    with pytest.raises(ValidationError, match="duration_minutes"):
        ModelCandidateOrders.model_validate(
            {
                "orders": [
                    {
                        "strategy": "balanced",
                        "place_ids": ["place-1"],
                        "duration_minutes": 30,
                    }
                ]
            }
        )


def test_model_orders_reject_unknown_place_ids() -> None:
    """LLM은 승인된 후보 풀에 없는 장소 ID를 순서에 추가할 수 없어야 한다."""

    model = ModelCandidateOrders.model_validate(
        {
            "orders": [
                {"strategy": strategy, "place_ids": ["unknown"]}
                for strategy in ("balanced", "relaxed", "experience_max")
            ]
        }
    )
    with pytest.raises(ValueError, match="MODEL_UNKNOWN_PLACE"):
        model.validate_known_places({"known"})
