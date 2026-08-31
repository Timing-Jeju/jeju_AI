"""부분 실데이터 후보 순서 preview의 운영 격리 테스트."""

from __future__ import annotations

import pytest

from jeju_trip.application.staging_candidate_preview import (
    StagingPlace,
    build_staging_candidate_preview,
)


def _place(fact_id: str, name: str) -> StagingPlace:
    return StagingPlace(place_fact_id=fact_id, name=name, category="12")


def test_partial_candidate_preview_is_never_a_production_recommendation() -> None:
    """부분 후보 세 개는 확인용 순서만 제공하고 성공 추천이나 이동 수치를 만들지 않아야 한다."""

    accommodation = StagingPlace("tourapi.place:hotel", "시험 숙소", "32")
    required = _place("tourapi.place:required", "필수 장소")
    a, b, c = (_place(f"tourapi.place:{value}", value) for value in ("a", "b", "c"))
    preview = build_staging_candidate_preview(
        accommodation=accommodation,
        candidate_orders={
            "balanced": (required, a, b),
            "relaxed": (required, b, c),
            "experience_max": (required, c, a),
        },
        required_place_ids=frozenset({required.place_fact_id}),
        parseable_hours_ids=frozenset({a.place_fact_id}),
        completed_intro_ids=frozenset({required.place_fact_id, a.place_fact_id, b.place_fact_id}),
        source_completion_ratio=0.92,
    )

    payload = preview.as_dict()
    assert payload["status"] == "staging_candidate_preview"
    assert payload["production_recommendation"] is False
    assert len(payload["candidate_orders"]) == 3
    assert "timeline" not in repr(payload)
    assert "distance_meters" not in repr(payload)
    assert "duration_minutes" not in repr(payload)
    assert "cost_krw" not in repr(payload)
    statuses = {
        place["place_fact_id"]: place["opening_hours_status"]
        for candidate in payload["candidate_orders"]
        for place in candidate["places"]
    }
    assert statuses[a.place_fact_id] == "parseable_from_partial_snapshot"
    assert statuses[b.place_fact_id] == "unparseable_from_partial_snapshot"
    assert statuses[c.place_fact_id] == "source_query_missing"


def test_partial_candidate_preview_rejects_duplicate_orders() -> None:
    """관광지 순서가 같은 세 후보를 서로 다른 시험 결과처럼 포장하지 않아야 한다."""

    accommodation = StagingPlace("tourapi.place:hotel", "시험 숙소", "32")
    places = (_place("tourapi.place:a", "a"), _place("tourapi.place:b", "b"))

    with pytest.raises(ValueError, match="STAGING_CANDIDATE_ORDERS_NOT_DISTINCT"):
        build_staging_candidate_preview(
            accommodation=accommodation,
            candidate_orders={
                "balanced": places,
                "relaxed": places,
                "experience_max": places,
            },
            required_place_ids=frozenset(),
            parseable_hours_ids=frozenset(),
            completed_intro_ids=frozenset({place.place_fact_id for place in places}),
            source_completion_ratio=0.92,
        )
