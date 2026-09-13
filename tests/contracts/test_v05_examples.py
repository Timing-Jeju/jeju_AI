"""보관된 v0.5 예시와 현재 v0.7 synthetic 예시를 구분해 검증한다."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from jeju_trip.domain.models import (
    DayTripResponse,
    EvaluateJejuDayTripInput,
    EvaluationResponse,
    RecommendDayTripsInput,
    RevalidateJejuDayTripInput,
    RevalidationResponse,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs/examples/v0.5/east-poc"
CURRENT_EXAMPLES = ROOT / "docs/examples/v0.7/synthetic"


def _load(name: str):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_v05_input_is_archived_and_rejected_by_current_models() -> None:
    """보관된 v0.5 입력은 v0.7 공개 모델에 암묵 변환되지 않아야 한다."""

    with pytest.raises(ValidationError):
        RecommendDayTripsInput.model_validate(_load("recommend.input.json"))


def test_all_v07_synthetic_examples_validate_against_public_models() -> None:
    """추천·평가·정상·지연 예시는 모두 v0.7 계약을 통과해야 한다."""

    def current(name: str):
        return json.loads((CURRENT_EXAMPLES / name).read_text(encoding="utf-8"))

    RecommendDayTripsInput.model_validate(current("recommend.input.json"))
    DayTripResponse.model_validate(current("recommend.output.json"))
    TypeAdapter(EvaluateJejuDayTripInput).validate_python(current("evaluate.input.json"))
    EvaluationResponse.model_validate(current("evaluate.output.json"))
    RevalidateJejuDayTripInput.model_validate(current("revalidate-normal.input.json"))
    RevalidationResponse.model_validate(current("revalidate-normal.output.json"))
    RevalidateJejuDayTripInput.model_validate(current("revalidate-delay.input.json"))
    RevalidationResponse.model_validate(current("revalidate-delay.output.json"))


def test_example_manifest_marks_synthetic_data_and_archived_schema() -> None:
    """v0.5 manifest는 live fact가 아님과 당시 schema 식별자를 보존해야 한다."""

    manifest = _load("artifact-manifest.json")
    assert manifest["mode"] == "synthetic_contract_example"
    assert manifest["operational_fact"] is False
    assert manifest["contains_live_tmap_or_tago_payload"] is False
    assert manifest["schema_version"] == "0.5.0"
    assert len(manifest["schema_sha256"]) == len(hashlib.sha256().hexdigest())


def test_v07_synthetic_bus_timeline_matches_door_to_door_components() -> None:
    """합성 버스의 전체 시간과 대기는 접근 도보·예정 운행·도착 도보와 일치해야 한다."""

    response = DayTripResponse.model_validate_json(
        (CURRENT_EXAMPLES / "recommend.output.json").read_text(encoding="utf-8")
    )
    bus_events = [
        event
        for candidate in response.recommendations
        for event in candidate.timeline
        if event.transfer is not None and event.transfer.mode == "bus"
    ]
    assert bus_events
    for event in bus_events:
        transfer = event.transfer
        assert transfer is not None
        assert transfer.access_walk is not None
        assert transfer.egress_walk is not None
        assert transfer.mode_decision is not None
        first, last = transfer.bus_rides[0], transfer.bus_rides[-1]
        stop_arrival = event.start_at + timedelta(minutes=transfer.access_walk.planned_minutes)
        assert event.end_at == last.scheduled_arrival_at + timedelta(
            minutes=transfer.egress_walk.planned_minutes
        )
        assert (
            transfer.mode_decision.bus_wait_minutes
            == (first.scheduled_departure_at - stop_arrival).total_seconds() / 60
        )
