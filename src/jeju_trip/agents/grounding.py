"""LLM이 미지 근거나 수치를 도입하지 못하게 하는 경계."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from jeju_trip.domain.models import Strategy


@dataclass(frozen=True)
class ModelReason:
    text: str
    evidence_fact_ids: tuple[str, ...]


class ModelOrder(BaseModel):
    """LLM이 제안할 수 있는 전략별 장소 ID 순서."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy: Strategy
    place_ids: tuple[str, ...]


class ModelCandidateOrders(BaseModel):
    """시간·거리·비용 필드를 구조적으로 허용하지 않는 LLM 출력."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    orders: tuple[ModelOrder, ...]

    def validate_known_places(self, known_place_ids: set[str]) -> ModelCandidateOrders:
        unknown = {
            place_id
            for order in self.orders
            for place_id in order.place_ids
            if place_id not in known_place_ids
        }
        if unknown:
            raise ValueError(f"MODEL_UNKNOWN_PLACE:{','.join(sorted(unknown))}")
        if {order.strategy for order in self.orders} != set(Strategy):
            raise ValueError("MODEL_STRATEGY_SET_INVALID")
        return self


def validate_model_reasons(
    reasons: tuple[ModelReason, ...], known_fact_ids: set[str]
) -> tuple[ModelReason, ...]:
    for reason in reasons:
        if not reason.evidence_fact_ids:
            raise ValueError("MODEL_REASON_WITHOUT_EVIDENCE")
        unknown = set(reason.evidence_fact_ids) - known_fact_ids
        if unknown:
            raise ValueError(f"MODEL_UNKNOWN_EVIDENCE:{','.join(sorted(unknown))}")
    return reasons


def merge_structured_over_original(
    structured: dict[str, object], parsed_original: dict[str, object]
) -> dict[str, object]:
    """원문 보충값을 먼저 놓고 구조화 필드로 덮어쓴다."""

    return {**parsed_original, **structured}
