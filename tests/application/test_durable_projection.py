"""영속 일정 경계의 닫힌 정규화 projection 검증."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from jeju_trip.application.durable_projection import project_schedule_candidates
from jeju_trip.domain.durable_projection import DurableCandidateSet
from tests.factories import make_success_response


def test_persisted_candidates_share_stay_duration_diversity_rule() -> None:
    """동일 장소·수단에서도 체류시간이 다른 정상 후보의 다양성을 유지해야 한다."""
    payload = project_schedule_candidates(make_success_response()).model_dump(mode="json")
    first = payload["candidates"][0]
    for index, candidate in enumerate(payload["candidates"][1:], start=1):
        candidate["place_ids"] = first["place_ids"]
        candidate["events"] = deepcopy(first["events"])
        candidate["totals"] = deepcopy(first["totals"])
        candidate["segment_risks"] = deepcopy(first["segment_risks"])
        shifted = False
        for event in candidate["events"]:
            if shifted:
                event["start_at"] = (
                    datetime.fromisoformat(event["start_at"]) + timedelta(minutes=index)
                ).isoformat()
            if not shifted and event["type"] in {"visit", "meal", "rest"}:
                shifted = True
                event["duration_minutes"] += index
                candidate["totals"][f"{event['type']}_minutes"] += index
                candidate["totals"]["total_minutes"] += index
            if shifted:
                event["end_at"] = (
                    datetime.fromisoformat(event["end_at"]) + timedelta(minutes=index)
                ).isoformat()
    assert len(DurableCandidateSet.model_validate(payload).candidates) == 3


@pytest.mark.parametrize("mutation", ["expiry", "totals", "diversity", "cycle"],
                         ids=["역순만료", "합계오염", "동일후보", "순환근거"])
def test_projection_rejects_corrupt_persisted_result(mutation: str) -> None:
    """저장 후 오염된 시간·합계·다양성·근거 순환을 읽기 경계에서 거부해야 한다."""
    payload = project_schedule_candidates(make_success_response()).model_dump(mode="json")
    if mutation == "expiry":
        payload["expires_at"] = "2000-01-01T00:00:00"
    elif mutation == "totals":
        payload["candidates"][0]["totals"]["visit_minutes"] = 999999
    elif mutation == "diversity":
        for candidate in payload["candidates"][1:]:
            candidate["place_ids"] = payload["candidates"][0]["place_ids"]
            candidate["events"] = payload["candidates"][0]["events"]
            candidate["totals"] = payload["candidates"][0]["totals"]
            candidate["segment_risks"] = payload["candidates"][0]["segment_risks"]
    else:
        for fact in payload["provenance"]:
            fact["source_ids"] = []
            fact["input_fact_ids"] = [fact["fact_id"]]
    with pytest.raises(ValidationError):
        DurableCandidateSet.model_validate(payload)


def test_projection_preserves_three_strategies_and_original_times() -> None:
    """세 전략과 검증된 시각·합계만 복사하고 재계산하지 않아야 한다."""
    response = make_success_response()
    result = project_schedule_candidates(response)
    assert len(result.candidates) == 3
    for candidate, original in zip(result.candidates, response.recommendations, strict=True):
        assert candidate.strategy == original.strategy
        assert candidate.totals == original.totals
        assert candidate.events[0].start_at == original.timeline[0].start_at
    assert DurableCandidateSet.model_validate_json(result.model_dump_json()) == result


def test_projection_does_not_serialize_arbitrary_evidence_values() -> None:
    """임의 evidence 값이나 요청 원문을 영속 projection으로 복사하지 않아야 한다."""
    result = project_schedule_candidates(make_success_response())
    for fact in result.provenance:
        assert set(fact.model_dump()) == {"fact_id", "source_ids", "input_fact_ids"}
    assert "request" not in result.model_dump()


def test_projection_rejects_unknown_fields_and_partial_candidates() -> None:
    """원본 필드 추가와 후보 일부 노출은 역직렬화에서도 거부해야 한다."""
    payload = project_schedule_candidates(make_success_response()).model_dump(mode="json")
    with pytest.raises(ValidationError):
        DurableCandidateSet.model_validate({**payload, "raw_response": {}})
    payload["candidates"] = payload["candidates"][:2]
    with pytest.raises(ValidationError):
        DurableCandidateSet.model_validate(payload)


def test_projection_rejects_broken_fact_lineage() -> None:
    """영속 결과의 근거 계보가 끊기면 복구 가능한 후보로 읽지 않아야 한다."""
    payload = project_schedule_candidates(make_success_response()).model_dump(mode="json")
    payload["provenance"][0]["input_fact_ids"] = ["missing-fact"]
    with pytest.raises(ValidationError):
        DurableCandidateSet.model_validate(payload)
