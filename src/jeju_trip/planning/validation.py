"""모델 생성 전후에도 재사용하는 결정론적 일정 검증."""

from __future__ import annotations

from collections import Counter

from jeju_trip.domain.models import (
    Recommendation,
    Strategy,
    recommendations_are_materially_different,
)


def validate_three_diverse_recommendations(
    recommendations: tuple[Recommendation, ...],
) -> tuple[str, ...]:
    failures: list[str] = []
    if len(recommendations) != 3:
        failures.append("RECOMMENDATION_COUNT_NOT_THREE")
    counts = Counter(item.strategy for item in recommendations)
    if set(counts) != set(Strategy) or any(count != 1 for count in counts.values()):
        failures.append("STRATEGY_SET_INVALID")
    if any(
        not recommendations_are_materially_different(first, second)
        for index, first in enumerate(recommendations)
        for second in recommendations[index + 1 :]
    ):
        failures.append("ROUTES_NOT_DIVERSE")
    return tuple(failures)
