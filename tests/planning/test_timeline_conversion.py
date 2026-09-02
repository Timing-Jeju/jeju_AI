"""생성 일정의 full-timeline 무손실 변환 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jeju_trip.domain.models import (
    CostRange,
    EndpointBasis,
    FullTimelineBuffer,
    ModeDecision,
    RouteAlternativeSummary,
    TimelineEvent,
)
from jeju_trip.planning.generation import DeterministicDayTripGenerator
from jeju_trip.planning.policy import load_planning_policy
from jeju_trip.planning.timeline_conversion import recommendation_to_full_timeline
from tests.planning.test_generation import FixedGenerationGateway, FixedOrderProposer, _request

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[2]


def test_conversion_preserves_mode_decision_alternatives_and_buffer_reason() -> None:
    """생성 타임라인을 판정 입력으로 바꿀 때 수단 비교와 택시 대기 이유를 잃지 않아야 한다."""

    request = _request()
    response = DeterministicDayTripGenerator(
        FixedGenerationGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=datetime(2026, 8, 11, 12, tzinfo=KST))
    recommendation = response.recommendations[0]
    transfer_index = next(
        index for index, event in enumerate(recommendation.timeline) if event.type == "transfer"
    )
    original_transfer_event = recommendation.timeline[transfer_index]
    assert original_transfer_event.transfer is not None
    fact_id = original_transfer_event.evidence_fact_ids[0]
    alternative = RouteAlternativeSummary(
        mode="bus",
        duration_minutes=None,
        walking_minutes=0,
        cost=CostRange(min_krw=1_200, max_krw=1_200, is_estimated=False),
        status="unverifiable",
        reason_codes=("BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED",),
        evidence_fact_ids=(fact_id,),
    )
    decision = ModeDecision(
        policy_id="cost-time-balance-v1",
        selected_mode=original_transfer_event.transfer.mode,
        origin_basis=EndpointBasis.VERIFIED_ENTRANCE,
        destination_basis=EndpointBasis.VERIFIED_ENTRANCE,
        reason_codes=("TAXI_BUS_SAVINGS_BELOW_5000_KRW",),
        evidence_fact_ids=(fact_id,),
    )
    changed_transfer = original_transfer_event.model_copy(
        update={
            "transfer": original_transfer_event.transfer.model_copy(
                update={"alternatives": (alternative,), "mode_decision": decision}
            )
        }
    )
    buffer_start = changed_transfer.end_at
    buffer = TimelineEvent(
        event_id="pickup-policy-buffer",
        sequence=changed_transfer.sequence + 1,
        type="buffer",
        start_at=buffer_start,
        end_at=buffer_start + timedelta(minutes=10),
        duration_minutes=10,
        title="택시 호출 계획 대기",
        reason_code="TAXI_PICKUP_PLANNING_BUFFER_APPLIED",
        evidence_fact_ids=(fact_id,),
    )
    shifted = []
    for index, event in enumerate(recommendation.timeline):
        if index < transfer_index:
            shifted.append(event)
        elif index == transfer_index:
            shifted.extend((changed_transfer, buffer))
        else:
            shifted.append(
                event.model_copy(
                    update={
                        "sequence": event.sequence + 1,
                        "start_at": event.start_at + timedelta(minutes=10),
                        "end_at": event.end_at + timedelta(minutes=10),
                    }
                )
            )
    changed = recommendation.model_copy(
        update={
            "timeline": tuple(shifted),
            "day_end_at": recommendation.day_end_at + timedelta(minutes=10),
            "accommodation_return_at": recommendation.accommodation_return_at
            + timedelta(minutes=10),
            "totals": recommendation.totals.model_copy(
                update={
                    "total_minutes": recommendation.totals.total_minutes + 10,
                    "buffer_minutes": recommendation.totals.buffer_minutes + 10,
                }
            ),
        }
    )

    converted = recommendation_to_full_timeline(request, changed)
    converted_transfer = next(item for item in converted.timeline if item.type == "transfer")
    converted_buffer = next(item for item in converted.timeline if item.event_id == buffer.event_id)
    assert converted_transfer.route_alternatives == (alternative,)
    assert converted_transfer.mode_decision == decision
    assert isinstance(converted_buffer, FullTimelineBuffer)
    assert converted_buffer.reason_code == "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
