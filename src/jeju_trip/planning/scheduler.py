"""하드 제약을 통과한 후보에서 세 전략의 서로 다른 최적안을 선택한다."""

from __future__ import annotations

from dataclasses import dataclass

from jeju_trip.domain.models import Recommendation, Strategy
from jeju_trip.planning.validation import validate_three_diverse_recommendations


@dataclass(frozen=True)
class ScoredCandidate:
    recommendation: Recommendation
    score: float
    hard_constraint_failures: tuple[str, ...] = ()


class InsufficientFeasibleRoutes(ValueError):
    """유효하고 서로 다른 세 전략을 선택할 수 없는 경우."""


def select_three_routes(candidates: tuple[ScoredCandidate, ...]) -> tuple[Recommendation, ...]:
    selected: list[Recommendation] = []
    for strategy in Strategy:
        feasible = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.recommendation.strategy == strategy
                and not candidate.hard_constraint_failures
            ),
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        if not feasible:
            raise InsufficientFeasibleRoutes("insufficient_feasible_routes")
        selected.append(feasible[0].recommendation)
    failures = validate_three_diverse_recommendations(tuple(selected))
    if failures:
        raise InsufficientFeasibleRoutes(f"insufficient_feasible_routes:{','.join(failures)}")
    return tuple(selected)
