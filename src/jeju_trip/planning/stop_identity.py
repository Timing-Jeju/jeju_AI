"""provider 정류장 ID를 보존하는 canonical mapping 판정."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MappingMethod = Literal["OFFICIAL_ID", "COORDINATE_AND_NAME", "ROUTE_SEQUENCE", "CURATED"]
MappingStatus = Literal["CONFIRMED", "REVIEW_REQUIRED", "REJECTED"]


@dataclass(frozen=True)
class StopIdentityCandidate:
    canonical_stop_id: str
    provider: str
    provider_stop_id: str
    normalized_name: str
    coordinate_distance_meters: float | None
    route_sequence_matches: bool
    official_id_matches: bool


@dataclass(frozen=True)
class StopIdentityDecision:
    method: MappingMethod
    status: MappingStatus
    confidence: float


def decide_stop_identity(candidate: StopIdentityCandidate) -> StopIdentityDecision:
    if candidate.official_id_matches:
        return StopIdentityDecision("OFFICIAL_ID", "CONFIRMED", 1.0)
    if (
        candidate.route_sequence_matches
        and candidate.coordinate_distance_meters is not None
        and candidate.coordinate_distance_meters <= 30
    ):
        return StopIdentityDecision("ROUTE_SEQUENCE", "CONFIRMED", 0.95)
    if candidate.coordinate_distance_meters is not None:
        if candidate.coordinate_distance_meters <= 20:
            return StopIdentityDecision("COORDINATE_AND_NAME", "REVIEW_REQUIRED", 0.8)
        if candidate.coordinate_distance_meters > 100:
            return StopIdentityDecision("COORDINATE_AND_NAME", "REJECTED", 0.1)
    return StopIdentityDecision("COORDINATE_AND_NAME", "REVIEW_REQUIRED", 0.3)
