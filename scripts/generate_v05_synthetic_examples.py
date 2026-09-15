"""v0.5 동부 PoC 계약 예시를 synthetic adapter와 Pydantic으로 생성한다."""

# ruff: noqa: E402 -- 저장소 test fixture를 deterministic synthetic adapter로 재사용한다.

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Set
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel

IMPORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(IMPORT_ROOT))

from jeju_trip.domain.models import (
    BusRide,
    Coordinates,
    CostRange,
    Derivation,
    EndpointBasis,
    EvidenceFact,
    FullTimelineTransfer,
    ModeDecision,
    ProgressInput,
    RecoveryOption,
    RevalidateJejuDayTripInput,
    RevalidationResponse,
    Strategy,
    Transfer,
    WalkConnection,
)
from jeju_trip.planning.evaluation import ItineraryEvaluationEngine, RouteEvidence
from jeju_trip.planning.generation import DeterministicDayTripGenerator, VerifiedRouteOption
from jeju_trip.planning.policy import load_planning_policy
from jeju_trip.planning.timeline_conversion import recommendation_to_full_timeline
from tests.planning.test_generation import FixedGenerationGateway, FixedOrderProposer, _request

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/examples/v0.5/east-poc"
EXAMPLE_SCHEMA_VERSION = "0.5.0"
GENERATOR_NAME = "scripts/generate_v05_synthetic_examples.py"
KST = timezone(timedelta(hours=9))
GENERATED_AT = datetime(2026, 8, 11, 12, tzinfo=KST)


class SyntheticEastGateway(FixedGenerationGateway):
    """balanced 첫 구간에만 공식 시간표 모양의 synthetic 버스를 제공한다."""

    def route(self, from_id, to_id, departure_at, strategy, request, budget):
        if strategy != Strategy.BALANCED or (from_id, to_id) != ("hotel", "required"):
            return super().route(from_id, to_id, departure_at, strategy, request, budget)
        budget.claim_external_call("synthetic-bus-routing")
        fact_id = "fact-route-hotel-required"
        access = WalkConnection(
            kind="access_walk",
            from_id="entrance-hotel",
            to_id="stop-board",
            distance_meters=200,
            expected_minutes=4,
            speed_multiplier=1.0,
            route_uncertainty_minutes=1,
            planned_minutes=5,
            entrance_verification="VERIFIED",
            evidence_fact_ids=(fact_id,),
        )
        egress = WalkConnection(
            kind="egress_walk",
            from_id="stop-alight",
            to_id="entrance-required",
            distance_meters=200,
            expected_minutes=4,
            speed_multiplier=1.0,
            route_uncertainty_minutes=1,
            planned_minutes=5,
            entrance_verification="VERIFIED",
            evidence_fact_ids=(fact_id,),
        )
        scheduled_departure = departure_at + timedelta(minutes=20)
        scheduled_arrival = departure_at + timedelta(minutes=35)
        ride = BusRide(
            canonical_boarding_stop_id="stop-board",
            provider_boarding_stop_id="provider-stop-board",
            boarding_stop_name="합성 승차 정류장",
            boarding_stop_position=Coordinates(latitude=33.45, longitude=126.7),
            boarding_direction="동쪽",
            canonical_alighting_stop_id="stop-alight",
            provider_alighting_stop_id="provider-stop-alight",
            alighting_stop_name="합성 하차 정류장",
            alighting_stop_position=Coordinates(latitude=33.46, longitude=126.8),
            route_id="route-synthetic-201",
            route_number="201",
            scheduled_departure_at=scheduled_departure,
            scheduled_arrival_at=scheduled_arrival,
            recommended_stop_arrival_at=scheduled_departure - timedelta(minutes=7),
            boarding_buffer_minutes=7,
            mapping_status="CONFIRMED",
            distance_meters=13_000,
            route_stop_polyline_estimate=True,
            distance_derivation_fact_ids=(fact_id,),
            evidence_fact_ids=(fact_id,),
        )
        decision = ModeDecision(
            selected_mode="bus",
            origin_basis=EndpointBasis.VERIFIED_ENTRANCE,
            destination_basis=EndpointBasis.VERIFIED_ENTRANCE,
            planned_walk_minutes=10,
            bus_wait_minutes=15,
            deadline_slack_minutes=20,
            reason_codes=("BUS_WITHIN_30_MINUTES",),
            evidence_fact_ids=(fact_id,),
        )
        return VerifiedRouteOption(
            from_id=from_id,
            to_id=to_id,
            duration_minutes=40,
            walking_minutes=10,
            walking_distance_meters=400,
            cost_min_krw=1_200,
            cost_max_krw=1_200,
            transfers=0,
            transfer=Transfer(
                mode="bus",
                access_walk=access,
                bus_rides=(ride,),
                egress_walk=egress,
                distance_meters=13_400,
                distance_is_estimated=True,
                mode_decision=decision,
            ),
            evidence_fact_ids=(fact_id,),
        )


class SyntheticEvaluationEvidence:
    """생성 숫자를 재사용하지 않고 같은 synthetic source를 다시 조회한다."""

    def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
        if mode == "bus":
            return RouteEvidence(
                mode="bus",
                duration_minutes=40,
                distance_meters=13_400,
                cost_krw=1_200,
                walking_minutes=10,
                walking_distance_meters=400,
                transfers=0,
                evidence_fact_ids=("fact-route-hotel-required",),
                route_number="201",
                provider_route_id="route-synthetic-201",
                boarding_stop_id="stop-board",
                alighting_stop_id="stop-alight",
                scheduled_departure_at=departure_at + timedelta(minutes=20),
                scheduled_arrival_at=departure_at + timedelta(minutes=35),
            )
        return self.route(from_place_id, to_place_id, departure_at)

    def route(self, from_place_id, to_place_id, departure_at):
        return RouteEvidence(
            mode="walk",
            duration_minutes=15,
            distance_meters=500,
            cost_krw=0,
            walking_minutes=15,
            walking_distance_meters=500,
            transfers=0,
            evidence_fact_ids=(f"fact-route-{from_place_id}-{to_place_id}",),
        )

    def opening_window(self, place_id, on_date):
        return (
            datetime.combine(on_date, datetime.min.time(), tzinfo=KST) + timedelta(hours=9),
            datetime.combine(on_date, datetime.min.time(), tzinfo=KST) + timedelta(hours=19),
            (f"fact-{place_id}",),
        )

    def evidence_facts(self):
        """합성 경로와 운영시간 ID에 대응하는 결정론적 fact ledger를 제공한다."""

        fact_ids = {
            "fact-route-hotel-required",
            "fact-route-required-meal-a",
            "fact-route-required-meal",
            "fact-route-meal-a-rest",
            "fact-route-meal-a",
            "fact-route-rest-hotel",
            "fact-route-a-rest",
            "fact-synthetic-taxi",
            "fact-required",
            "fact-meal-a",
            "fact-rest",
        }
        return tuple(
            EvidenceFact(
                fact_id=fact_id,
                category="synthetic_test_evidence",
                value={"synthetic": True},
                data_as_of=GENERATED_AT,
                retrieved_at=GENERATED_AT,
                confidence=1,
                is_estimated=True,
                derivation=Derivation(kind="policy"),
            )
            for fact_id in sorted(fact_ids)
        )

def _canonical_json_value(value):
    """set 순서와 Python 날짜형을 고정해 예시 checksum을 프로세스마다 같게 만든다."""

    if isinstance(value, BaseModel):
        return _canonical_json_value(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, Set) and not isinstance(value, (str, bytes)):
        return sorted(_canonical_json_value(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canonical_json_value(value.value)
    return value


def _dump(filename: str, value) -> None:
    payload = _canonical_json_value(value)
    (OUTPUT / filename).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    request = _request()
    response = DeterministicDayTripGenerator(
        SyntheticEastGateway(),
        FixedOrderProposer(),
        load_planning_policy(ROOT / "config/policies/planning_policy_v1.toml"),
    ).generate(request, now=GENERATED_AT)
    if response.status != "success":
        raise RuntimeError("SYNTHETIC_RECOMMENDATION_FAILED")
    response = response.model_copy(
        update={"request_id": f"req-synthetic-{EXAMPLE_SCHEMA_VERSION}"}
    )
    balanced = next(item for item in response.recommendations if item.strategy == Strategy.BALANCED)
    evaluation_input = recommendation_to_full_timeline(request, balanced)
    evaluation = ItineraryEvaluationEngine(SyntheticEvaluationEvidence()).evaluate(evaluation_input)
    if evaluation.status not in {"feasible", "feasible_with_caution"} or not (
        evaluation.schedule_window_fit
    ):
        raise RuntimeError("SYNTHETIC_EVALUATION_NOT_FEASIBLE_LOW")

    first_visit = next(item for item in balanced.timeline if item.type == "visit")
    normal_input = RevalidateJejuDayTripInput(
        checked_at=first_visit.start_at,
        progress=ProgressInput(
            state="at_place",
            current_event_id=first_visit.event_id,
            actual_time=first_visit.start_at,
            current_place_id=first_visit.place_id,
            current_event_started_at=first_visit.start_at,
        ),
        itinerary=evaluation_input,
    )
    normal_output = RevalidationResponse(
        status=cast(
            Literal["on_schedule", "at_risk", "disrupted", "data_unavailable"],
            evaluation.timing_status,
        ),
        timing_status=evaluation.timing_status,
        evidence_status=evaluation.evidence_status,
        checked_at=normal_input.checked_at,
        delay_minutes=0,
        location_basis="event",
        original_evaluation=evaluation,
        remaining_evaluation=evaluation,
    )

    first_transfer = next(
        item for item in evaluation_input.timeline if isinstance(item, FullTimelineTransfer)
    )
    if first_transfer.scheduled_departure_at is None:
        raise RuntimeError("SYNTHETIC_BUS_DEPARTURE_MISSING")
    delayed_at = first_transfer.scheduled_departure_at + timedelta(minutes=31)
    delay_input = RevalidateJejuDayTripInput(
        checked_at=delayed_at,
        progress=ProgressInput(
            state="waiting_bus",
            current_event_id=first_transfer.event_id,
            actual_time=delayed_at,
            current_stop_id=first_transfer.planned_boarding_stop_id,
            current_route_id=first_transfer.planned_route_id,
        ),
        itinerary=evaluation_input,
    )
    taxi_transfer = FullTimelineTransfer(
        event_id="recovery-taxi-transfer",
        type="transfer",
        start_at=delayed_at,
        end_at=delayed_at + timedelta(minutes=25),
        from_place=first_transfer.from_place,
        to_place=first_transfer.to_place,
        planned_mode="taxi",
        planned_distance_meters=13_000,
    )
    delay_output = RevalidationResponse(
        status="at_risk",
        timing_status="at_risk",
        evidence_status=evaluation.evidence_status,
        checked_at=delayed_at,
        delay_minutes=31,
        location_basis="event",
        original_evaluation=evaluation,
        remaining_evaluation=evaluation,
        recovery_options=(
            RecoveryOption(
                recovery_id="recovery-take-taxi",
                action="TAKE_TAXI",
                affected_event_ids=(first_transfer.event_id,),
                result_status="at_risk",
                evidence_fact_ids=("fact-synthetic-taxi",),
                replacement_transfer=taxi_transfer,
                expected_duration_minutes=25,
                expected_distance_meters=13_000,
                expected_cost=CostRange(min_krw=20_000, max_krw=25_000, is_estimated=True),
                reason_codes=(
                    "BUS_NO_DEPARTURE_WITHIN_30_MINUTES",
                    "TAXI_RECOVERY_PRESERVES_NEXT_DEADLINE",
                ),
                revalidated_evaluation=evaluation,
            ),
        ),
    )

    artifacts = {
        "recommend.input.json": request,
        "recommend.output.json": response,
        "evaluate.input.json": evaluation_input,
        "evaluate.output.json": evaluation,
        "revalidate-normal.input.json": normal_input,
        "revalidate-normal.output.json": normal_output,
        "revalidate-delay.input.json": delay_input,
        "revalidate-delay.output.json": delay_output,
    }
    for filename, value in artifacts.items():
        _dump(filename, value)
    schema = (ROOT / "docs/contracts/day-trip-recommendations.schema.json").read_bytes()
    _dump(
        "artifact-manifest.json",
        {
            "mode": "synthetic_contract_example",
            "generator": GENERATOR_NAME,
            "schema_version": EXAMPLE_SCHEMA_VERSION,
            "schema_sha256": hashlib.sha256(schema).hexdigest(),
            "operational_fact": False,
            "contains_live_tmap_or_tago_payload": False,
            "generated_on": date(2026, 8, 11).isoformat(),
            "files": sorted(artifacts),
        },
    )


if __name__ == "__main__":
    main()
