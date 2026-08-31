"""검증 전 장소 중심점을 출입구와 분리해 관리하는 staging 모델."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StagingEntrancePlace:
    place_fact_id: str
    name: str
    category: str

    def __post_init__(self) -> None:
        if not self.place_fact_id.startswith("tourapi.place:") or not self.name.strip():
            raise ValueError("STAGING_ENTRANCE_PLACE_INVALID")


@dataclass(frozen=True)
class StagingEntranceCandidates:
    places: tuple[StagingEntrancePlace, ...]

    def as_dict(self) -> dict[str, Any]:
        """좌표와 이동수단을 노출하지 않고 후속 검증 대상을 직렬화한다."""

        return {
            "status": "staging_entrance_candidates",
            "production_activation_allowed": False,
            "items": [
                {
                    "place_fact_id": place.place_fact_id,
                    "name": place.name,
                    "category": place.category,
                    "candidate_basis": "tourapi_place_center_reference",
                    "verification_status": "UNVERIFIED",
                    "usable_for_routing": False,
                    "required_follow_up": [
                        "OFFICIAL_OR_CURATED_ENTRANCE_SOURCE",
                        "PEDESTRIAN_ENDPOINT_CONFIRMATION",
                        "VEHICLE_PICKUP_ENDPOINT_CONFIRMATION",
                    ],
                }
                for place in self.places
            ],
        }


def build_staging_entrance_candidates(
    places: tuple[StagingEntrancePlace, ...],
) -> StagingEntranceCandidates:
    """중복 없는 장소 fact만 출입구 수동 검증 대기열로 만든다."""

    if not places:
        raise ValueError("STAGING_ENTRANCE_CANDIDATES_EMPTY")
    fact_ids = tuple(place.place_fact_id for place in places)
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("STAGING_ENTRANCE_CANDIDATE_DUPLICATED")
    return StagingEntranceCandidates(places)
