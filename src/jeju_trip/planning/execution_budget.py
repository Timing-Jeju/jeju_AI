"""기능별 외부 호출 수·동시성·하드 timeout 실행예산."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Lock

GENERATION_TIMEOUT_SECONDS = 150.0
EVALUATION_TIMEOUT_SECONDS = 90.0
REALTIME_TIMEOUT_SECONDS = 90.0


class PlanningBudgetExceeded(RuntimeError):
    """허용된 외부 경로 호출 수를 모두 사용한 경우."""


class PlanningTimeout(RuntimeError):
    """기능별 하드 timeout에 도달한 경우."""


@dataclass
class ExecutionBudget:
    timeout_seconds: float
    maximum_external_calls: int
    maximum_concurrency: int = 4
    allocation_limits: dict[str, int] = field(default_factory=dict)
    clock: Callable[[], float] = time.monotonic
    _started_at: float = field(init=False)
    _external_calls: int = field(default=0, init=False)
    _calls_by_source: dict[str, int] = field(default_factory=dict, init=False)
    _calls_by_allocation: dict[str, int] = field(default_factory=dict, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _call_slots: BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._started_at = self.clock()
        self._call_slots = BoundedSemaphore(self.maximum_concurrency)

    @classmethod
    def generation(
        cls,
        clock: Callable[[], float] = time.monotonic,
        *,
        bus_only: bool = False,
    ) -> ExecutionBudget:
        """버스 전용 요청은 총 40회 안에서 정류장 연결 보행에 전부 배정한다."""

        return cls(
            GENERATION_TIMEOUT_SECONDS,
            40,
            clock=clock,
            allocation_limits={
                "direct_walk": 10,
                "taxi_driving": 10,
                "bus_walk": 40 if bus_only else 20,
            },
        )

    @classmethod
    def evaluation(cls, clock: Callable[[], float] = time.monotonic) -> ExecutionBudget:
        return cls(EVALUATION_TIMEOUT_SECONDS, 30, clock=clock)

    @classmethod
    def realtime(cls, clock: Callable[[], float] = time.monotonic) -> ExecutionBudget:
        return cls(REALTIME_TIMEOUT_SECONDS, 12, clock=clock)

    @property
    def external_calls(self) -> int:
        return self._external_calls

    @property
    def calls_by_source(self) -> dict[str, int]:
        return dict(self._calls_by_source)

    def ensure_time_remaining(self) -> None:
        if self.clock() - self._started_at >= self.timeout_seconds:
            raise PlanningTimeout("PLANNING_TIMEOUT")

    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - (self.clock() - self._started_at))

    def claim_external_call(self, source_id: str, *, allocation: str | None = None) -> None:
        with self._lock:
            self.ensure_time_remaining()
            if self._external_calls >= self.maximum_external_calls:
                raise PlanningBudgetExceeded("ROUTING_BUDGET_EXHAUSTED")
            allocation_key = allocation or source_id
            allocation_limit = self.allocation_limits.get(allocation_key)
            allocation_count = self._calls_by_allocation.get(allocation_key, 0)
            if allocation_limit is not None and allocation_count >= allocation_limit:
                raise PlanningBudgetExceeded("ROUTING_BUDGET_EXHAUSTED")
            self._external_calls += 1
            self._calls_by_source[source_id] = self._calls_by_source.get(source_id, 0) + 1
            self._calls_by_allocation[allocation_key] = allocation_count + 1

    @contextmanager
    def call_slot(self) -> Iterator[None]:
        """실제 외부 I/O 동안만 전역 동시성 슬롯을 점유한다."""

        if not self._call_slots.acquire(timeout=self.remaining_seconds()):
            raise PlanningTimeout("PLANNING_TIMEOUT")
        try:
            self.ensure_time_remaining()
            yield
        finally:
            self._call_slots.release()

    @contextmanager
    def external_call(self, source_id: str) -> Iterator[None]:
        """단순 adapter가 호출 수와 동시성 제한을 함께 적용하도록 한다."""

        with self.call_slot():
            self.claim_external_call(source_id)
            yield
