"""세 전략 선택과 전체 실패 정책 테스트."""

from __future__ import annotations

import pytest

from jeju_trip.domain.models import Strategy
from jeju_trip.planning.scheduler import (
    InsufficientFeasibleRoutes,
    ScoredCandidate,
    select_three_routes,
)
from tests.factories import make_recommendation


def test_scheduler_returns_one_best_route_per_strategy() -> None:
    """스케줄러는 하드 제약을 통과한 전략별 최고 점수 경로를 하나씩 선택해야 한다."""

    candidates = tuple(
        ScoredCandidate(make_recommendation(strategy, rank), score=100 - rank)
        for rank, strategy in enumerate(Strategy, start=1)
    )
    selected = select_three_routes(candidates)
    assert tuple(item.strategy for item in selected) == tuple(Strategy)


def test_scheduler_fails_all_when_one_strategy_is_missing() -> None:
    """전략 하나의 유효 후보가 없으면 부분 성공 없이 전체 실패해야 한다."""

    candidates = (
        ScoredCandidate(make_recommendation(Strategy.BALANCED, 1), 90),
        ScoredCandidate(make_recommendation(Strategy.RELAXED, 2), 80),
    )
    with pytest.raises(InsufficientFeasibleRoutes, match="insufficient_feasible_routes"):
        select_three_routes(candidates)
