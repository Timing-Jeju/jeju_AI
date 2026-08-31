"""v0.5의 결정론적 service-priority 이동수단 선택 정책."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal


@dataclass(frozen=True)
class ModeCandidate:
    """외부 adapter와 공식 publication이 이미 계산한 수단 후보."""

    mode: Literal["walk", "bus", "taxi"]
    duration_minutes: int
    distance_meters: int
    arrival_at: datetime | None = None
    access_walk_minutes: int = 0
    scheduled_departure_at: datetime | None = None
    cost_max_krw: int = 0
    verified: bool = True
    reason_codes: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServicePriorityResult:
    """선택 결과이며 숫자를 새로 생성하지 않고 후보의 값을 참조한다."""

    selected_mode: Literal["walk", "bus", "taxi"] | None
    candidate: ModeCandidate | None
    planned_walk_minutes: int | None
    bus_wait_minutes: int | None
    deadline_slack_minutes: int | None
    reason_codes: tuple[str, ...]
    evidence_fact_ids: tuple[str, ...]
    blocking: bool = False


def _deadline_slack(candidate: ModeCandidate, deadline: datetime | None) -> int | None:
    if deadline is None or candidate.arrival_at is None:
        return None
    return int((deadline - candidate.arrival_at).total_seconds() // 60)


def select_service_priority_mode(
    *,
    departure_at: datetime,
    walk: ModeCandidate | None,
    bus: ModeCandidate | None,
    taxi: ModeCandidate | None,
    deadline: datetime | None,
    max_walk_minutes: int,
    boarding_buffer_minutes: int = 7,
    bus_wait_limit_minutes: int = 30,
) -> ServicePriorityResult:
    """도보→검증 버스→택시 순으로 deadline과 명시 한도를 적용한다."""

    walk_limit = min(15, max_walk_minutes)
    if walk is not None and walk.verified and walk.duration_minutes <= walk_limit:
        slack = _deadline_slack(walk, deadline)
        if slack is None or slack >= 0:
            return ServicePriorityResult(
                "walk",
                walk,
                walk.duration_minutes,
                None,
                slack,
                ("WALK_WITHIN_15_MINUTES",),
                walk.evidence_fact_ids,
            )

    bus_reason_codes: tuple[str, ...] = ()
    bus_wait: int | None = None
    if bus is not None:
        if not bus.verified:
            return ServicePriorityResult(
                None,
                None,
                None,
                None,
                None,
                bus.reason_codes or ("BUS_EVIDENCE_UNVERIFIED",),
                bus.evidence_fact_ids,
                blocking=True,
            )
        if bus.scheduled_departure_at is None or bus.arrival_at is None:
            return ServicePriorityResult(
                None,
                None,
                None,
                None,
                None,
                ("BUS_SCHEDULE_CLAIM_MISSING",),
                bus.evidence_fact_ids,
                blocking=True,
            )
        ready_at = departure_at + timedelta(
            minutes=bus.access_walk_minutes + boarding_buffer_minutes
        )
        bus_wait = int((bus.scheduled_departure_at - ready_at).total_seconds() // 60)
        slack = _deadline_slack(bus, deadline)
        if 0 <= bus_wait <= bus_wait_limit_minutes and (slack is None or slack >= 0):
            return ServicePriorityResult(
                "bus",
                bus,
                bus.access_walk_minutes,
                bus_wait,
                slack,
                ("BUS_WITHIN_30_MINUTES",),
                bus.evidence_fact_ids,
            )
        if bus_wait > bus_wait_limit_minutes:
            bus_reason_codes = ("BUS_NO_DEPARTURE_WITHIN_30_MINUTES",)
        elif bus_wait < 0:
            bus_reason_codes = ("BUS_BOARDING_BUFFER_NOT_MET",)
        else:
            bus_reason_codes = ("BUS_MISSES_NEXT_DEADLINE",)

    if taxi is not None and taxi.verified:
        slack = _deadline_slack(taxi, deadline)
        if slack is None or slack >= 0:
            return ServicePriorityResult(
                "taxi",
                taxi,
                None,
                bus_wait,
                slack,
                bus_reason_codes + ("TAXI_PRESERVES_DEADLINE",),
                taxi.evidence_fact_ids,
            )

    return ServicePriorityResult(
        None,
        None,
        None,
        bus_wait,
        None,
        bus_reason_codes or ("NO_FEASIBLE_MODE",),
        (),
        blocking=False,
    )
