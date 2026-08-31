"""원래 남은 일정 판정과 분리된 v0.5 회복안 생성기."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from jeju_trip.domain.models import (
    CostRange,
    EvaluationResponse,
    FullTimelineTransfer,
    RecoveryOption,
)


@dataclass(frozen=True)
class TaxiRecoveryEvidence:
    transfer: FullTimelineTransfer
    duration_minutes: int
    distance_meters: int
    fare: CostRange
    arrival_at: datetime
    evidence_fact_ids: tuple[str, ...]


class RecoveryPlanner:
    """재평가를 통과한 변경안만 원 일정과 별도로 노출한다."""

    def taxi_after_bus_timeout(
        self,
        *,
        affected_event_ids: tuple[str, ...],
        elapsed_after_planned_departure_minutes: int,
        next_bus_wait_minutes: int | None,
        next_deadline: datetime,
        taxi: TaxiRecoveryEvidence | None,
        revalidated_evaluation: EvaluationResponse | None,
    ) -> tuple[RecoveryOption, ...]:
        if elapsed_after_planned_departure_minutes < 31:
            return ()
        if next_bus_wait_minutes is not None and next_bus_wait_minutes <= 30:
            return ()
        if taxi is None:
            return ()
        if taxi.arrival_at > next_deadline:
            return ()
        if revalidated_evaluation is None or revalidated_evaluation.status not in {
            "feasible",
            "feasible_with_caution",
        }:
            return ()
        return (
            RecoveryOption(
                recovery_id="recovery-take-taxi",
                action="TAKE_TAXI",
                affected_event_ids=affected_event_ids,
                result_status=(
                    "on_schedule" if revalidated_evaluation.status == "feasible" else "at_risk"
                ),
                evidence_fact_ids=taxi.evidence_fact_ids,
                replacement_transfer=taxi.transfer,
                expected_duration_minutes=taxi.duration_minutes,
                expected_distance_meters=taxi.distance_meters,
                expected_cost=taxi.fare,
                reason_codes=(
                    "BUS_NO_DEPARTURE_WITHIN_30_MINUTES",
                    "TAXI_RECOVERY_PRESERVES_NEXT_DEADLINE",
                ),
                revalidated_evaluation=revalidated_evaluation,
            ),
        )
