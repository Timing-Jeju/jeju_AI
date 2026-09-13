"""전체 검증을 통과한 추천만 닫힌 저장용 필드로 복사한다."""

from jeju_trip.domain.durable_projection import (
    DurableCandidate,
    DurableCandidateSet,
    DurableEvent,
    FactProvenance,
)
from jeju_trip.domain.models import DayTripResponse


def project_schedule_candidates(response: DayTripResponse) -> DurableCandidateSet:
    """원본 모델을 재검증하고 숫자를 새로 계산하지 않는 projection을 만든다."""
    checked = DayTripResponse.model_validate(response.model_dump(mode="python"))
    if checked.status != "success":
        raise ValueError("CANDIDATE_RESPONSE_NOT_SUCCESSFUL")
    return DurableCandidateSet(
        request_id=checked.request_id,
        generated_at=checked.generated_at,
        expires_at=checked.planning_context.plan_expires_at,
        provenance=tuple(
            FactProvenance(
                fact_id=fact.fact_id,
                source_ids=tuple(ref.source_id for ref in fact.source_refs),
                input_fact_ids=fact.derivation.input_fact_ids,
            )
            for fact in checked.evidence_facts
        ),
        candidates=tuple(
            DurableCandidate(
                route_id=candidate.route_id,
                place_ids=candidate.place_ids,
                rank=candidate.rank,
                strategy=candidate.strategy,
                feasibility=candidate.feasibility,
                score=candidate.score.total,
                start_place_id=candidate.start_place_id,
                end_place_id=candidate.end_place_id,
                totals=candidate.totals,
                segment_risks=candidate.segment_risks,
                events=tuple(
                    DurableEvent(
                        event_id=event.event_id,
                        sequence=event.sequence,
                        type=event.type,
                        start_at=event.start_at,
                        end_at=event.end_at,
                        duration_minutes=event.duration_minutes,
                        place_id=event.place_id,
                        mode=event.transfer.mode if event.transfer else None,
                        distance_meters=(
                            event.transfer.distance_meters if event.transfer else None
                        ),
                        evidence_fact_ids=event.evidence_fact_ids,
                    )
                    for event in candidate.timeline
                ),
            )
            for candidate in checked.recommendations
        ),
    )
