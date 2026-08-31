"""운영 추천과 격리된 부분 데이터 장소 순서 preview."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

StrategyName = Literal["balanced", "relaxed", "experience_max"]
StagingActivityType = Literal["visit", "meal", "rest"]
STRATEGIES: tuple[StrategyName, ...] = ("balanced", "relaxed", "experience_max")


@dataclass(frozen=True)
class StagingPlace:
    place_fact_id: str
    name: str
    category: str
    activity_type: StagingActivityType = "visit"

    def __post_init__(self) -> None:
        if not self.place_fact_id.startswith("tourapi.place:") or not self.name.strip():
            raise ValueError("STAGING_PLACE_INVALID")


@dataclass(frozen=True)
class StagingCandidatePreview:
    accommodation: StagingPlace
    candidate_orders: Mapping[StrategyName, tuple[StagingPlace, ...]]
    parseable_hours_ids: frozenset[str]
    completed_intro_ids: frozenset[str]
    source_completion_ratio: float

    def _hours_status(self, place_fact_id: str) -> str:
        if place_fact_id in self.parseable_hours_ids:
            return "parseable_from_partial_snapshot"
        if place_fact_id in self.completed_intro_ids:
            return "unparseable_from_partial_snapshot"
        return "source_query_missing"

    def as_dict(self) -> dict[str, Any]:
        """시간·거리·비용 없이 장소 순서와 근거 공백만 직렬화한다."""

        return {
            "status": "staging_candidate_preview",
            "production_recommendation": False,
            "source_completion_ratio": self.source_completion_ratio,
            "accommodation": {
                "place_fact_id": self.accommodation.place_fact_id,
                "name": self.accommodation.name,
                "endpoint_status": "provisional_center_only",
            },
            "candidate_orders": [
                {
                    "strategy": strategy,
                    "places": [
                        {
                            "place_fact_id": place.place_fact_id,
                            "name": place.name,
                            "category": place.category,
                            "activity_type": place.activity_type,
                            "opening_hours_status": self._hours_status(place.place_fact_id),
                        }
                        for place in self.candidate_orders[strategy]
                    ],
                }
                for strategy in STRATEGIES
            ],
            "missing_evidence": [
                "VERIFIED_ENTRANCE",
                "PUBLISHED_OPENING_HOURS",
                "DOOR_TO_DOOR_ROUTE_FACT",
                "FULL_SOURCE_SNAPSHOT",
            ],
            "usage_restriction": "ORDER_INSPECTION_ONLY",
        }


def build_staging_candidate_preview(
    *,
    accommodation: StagingPlace,
    candidate_orders: Mapping[StrategyName, tuple[StagingPlace, ...]],
    required_place_ids: frozenset[str],
    parseable_hours_ids: frozenset[str],
    completed_intro_ids: frozenset[str],
    source_completion_ratio: float,
) -> StagingCandidatePreview:
    """기존 장소 fact로 세 후보 순서를 만들되 운영 가능성은 주장하지 않는다."""

    if set(candidate_orders) != set(STRATEGIES):
        raise ValueError("STAGING_STRATEGIES_INVALID")
    if not 0 < source_completion_ratio < 1:
        raise ValueError("STAGING_PARTIAL_COMPLETION_RATIO_REQUIRED")
    if not parseable_hours_ids.issubset(completed_intro_ids):
        raise ValueError("STAGING_PARSEABLE_HOURS_WITHOUT_QUERY")
    signatures: set[tuple[str, ...]] = set()
    for strategy in STRATEGIES:
        order = candidate_orders[strategy]
        signature = tuple(place.place_fact_id for place in order)
        if not signature or len(signature) != len(set(signature)):
            raise ValueError("STAGING_CANDIDATE_ORDER_INVALID")
        if not required_place_ids.issubset(signature):
            raise ValueError("STAGING_REQUIRED_PLACE_MISSING")
        signatures.add(signature)
    if len(signatures) != len(STRATEGIES):
        raise ValueError("STAGING_CANDIDATE_ORDERS_NOT_DISTINCT")
    return StagingCandidatePreview(
        accommodation=accommodation,
        candidate_orders=dict(candidate_orders),
        parseable_hours_ids=parseable_hours_ids,
        completed_intro_ids=completed_intro_ids,
        source_completion_ratio=source_completion_ratio,
    )
