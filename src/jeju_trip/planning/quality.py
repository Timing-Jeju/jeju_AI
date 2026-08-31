"""typed projection publication 전 blocking 품질 검사."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class QualityIssue:
    reason_code: str
    field_name: str
    blocking: bool = True


def preliminary_jeju_coordinate_check(
    latitude: float, longitude: float
) -> tuple[QualityIssue, ...]:
    """경계 polygon 검사 전 빠른 사각형 사전검사만 제공한다."""

    if not (33.10 <= latitude <= 33.65 and 126.05 <= longitude <= 126.95):
        return (QualityIssue("COORDINATE_OUTSIDE_JEJU_PREFILTER", "position"),)
    return ()


def validate_opening_period(opens_at: time, closes_at: time) -> tuple[QualityIssue, ...]:
    if closes_at <= opens_at:
        return (QualityIssue("OPENING_PERIOD_INVALID", "closes_at"),)
    return ()


def validate_route_sequences(sequences: list[int]) -> tuple[QualityIssue, ...]:
    if any(sequence <= 0 for sequence in sequences):
        return (QualityIssue("ROUTE_SEQUENCE_NOT_POSITIVE", "route_sequence"),)
    if len(sequences) != len(set(sequences)):
        return (QualityIssue("ROUTE_SEQUENCE_DUPLICATED", "route_sequence"),)
    return ()
