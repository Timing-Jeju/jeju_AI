"""외부 경로 호출 수와 하드 timeout 실행예산 테스트."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

import pytest

from jeju_trip.planning.execution_budget import (
    ExecutionBudget,
    PlanningBudgetExceeded,
    PlanningTimeout,
)


def test_generation_budget_rejects_forty_first_route_call() -> None:
    """일정 생성은 TMAP 계열 마흔 번째 다음 호출 전에 전체 실패해야 한다."""

    budget = ExecutionBudget.generation(clock=lambda: 0.0)
    for _ in range(40):
        budget.claim_external_call("tmap.pedestrian")
    with pytest.raises(PlanningBudgetExceeded, match="ROUTING_BUDGET_EXHAUSTED"):
        budget.claim_external_call("tmap.driving")


def test_generation_budget_stops_at_one_hundred_fifty_seconds() -> None:
    """일정 생성은 150초 timeout에 도달하면 검증되지 않은 후보를 버려야 한다."""

    moments = iter((100.0, 249.9, 250.0))
    budget = ExecutionBudget.generation(clock=lambda: next(moments))
    budget.ensure_time_remaining()
    with pytest.raises(PlanningTimeout, match="PLANNING_TIMEOUT"):
        budget.ensure_time_remaining()


def test_external_call_slots_enforce_global_concurrency() -> None:
    """중첩 후보 계산도 실행예산이 허용한 전역 동시 호출 수를 넘지 않아야 한다."""

    budget = ExecutionBudget(5, 10, maximum_concurrency=2)
    state_lock = Lock()
    active = 0
    peak = 0

    def invoke() -> None:
        nonlocal active, peak
        with budget.external_call("provider"):
            with state_lock:
                active += 1
                peak = max(peak, active)
            sleep(0.02)
            with state_lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=5) as executor:
        tuple(executor.map(lambda _: invoke(), range(5)))

    assert peak == 2
    assert budget.external_calls == 5


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("evaluation", 30), ("realtime", 12)],
    ids=["기존일정", "실시간"],
)
def test_feature_specific_call_budgets(kind: str, expected: int) -> None:
    """기존 일정과 실시간 기능은 각자 정한 외부 호출 한도를 지켜야 한다."""

    factory = getattr(ExecutionBudget, kind)
    budget = factory(clock=lambda: 0.0)
    for _ in range(expected):
        budget.claim_external_call("provider")
    with pytest.raises(PlanningBudgetExceeded):
        budget.claim_external_call("provider")


@pytest.mark.parametrize(
    ("allocation", "limit"),
    [("direct_walk", 10), ("taxi_driving", 10), ("bus_walk", 20)],
    ids=["직접도보", "택시주행", "버스연결도보"],
)
def test_generation_route_allocations_have_independent_caps(allocation: str, limit: int) -> None:
    """생성 경로 예산은 직접 도보·택시 주행·버스 연결 도보 상한을 각각 강제해야 한다."""

    budget = ExecutionBudget.generation(clock=lambda: 0.0)
    for _ in range(limit):
        budget.claim_external_call("tmap", allocation=allocation)
    with pytest.raises(PlanningBudgetExceeded, match="ROUTING_BUDGET_EXHAUSTED"):
        budget.claim_external_call("tmap", allocation=allocation)


def test_bus_only_generation_can_use_all_forty_calls_for_stop_walks() -> None:
    """버스 전용 생성은 쓰지 않는 도보·택시 몫을 정류장 연결 보행 검증에 써야 한다."""

    budget = ExecutionBudget.generation(clock=lambda: 0.0, bus_only=True)
    for _ in range(40):
        budget.claim_external_call("tmap.pedestrian", allocation="bus_walk")
    with pytest.raises(PlanningBudgetExceeded, match="ROUTING_BUDGET_EXHAUSTED"):
        budget.claim_external_call("tmap.pedestrian", allocation="bus_walk")
