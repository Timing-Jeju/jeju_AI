"""제주 동부 scope publication 번들 회귀 테스트."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from jeju_trip.application.manual_import import (
    ScopeManifestBundle,
    validate_scope_manifest_bundle,
)
from jeju_trip.infrastructure.public_data_normalizers import (
    normalize_itinerary_template_candidates,
    normalize_itinerary_template_steps,
    normalize_place_opening_rules,
    normalize_place_schedule_exceptions,
    normalize_scope_members,
)

ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _bundle(version: str) -> ScopeManifestBundle:
    directory = ROOT / "docs" / "manifests" / version
    members = normalize_scope_members(_rows(directory / "service-scope-members.csv"))
    steps = normalize_itinerary_template_steps(_rows(directory / "itinerary-template-steps.csv"))
    candidates = normalize_itinerary_template_candidates(
        _rows(directory / "itinerary-template-candidates.csv")
    )
    assert not (*members.rejections, *steps.rejections, *candidates.rejections)
    return ScopeManifestBundle(members.records, steps.records, candidates.records)


def test_east_poc_v2_extends_places_without_losing_v1_members() -> None:
    """V2 scope는 V1 구성원을 모두 보존하고 함덕·월정리 장소를 명시적으로 추가해야 한다."""

    previous = _bundle("east-poc-v1")
    expanded = _bundle("east-poc-v2")
    validate_scope_manifest_bundle(expanded)

    previous_members = {(item.member_type, item.member_id) for item in previous.members}
    expanded_members = {(item.member_type, item.member_id) for item in expanded.members}
    assert previous_members <= expanded_members
    assert {
        ("PLACE", "tourapi.place:126451"),
        ("PLACE", "tourapi.place:2738701"),
    } <= expanded_members
    assert expanded.steps == previous.steps
    assert expanded.candidates == previous.candidates


def test_scope_manifest_can_preserve_multiple_complete_scopes() -> None:
    """하나의 active manifest는 기존 scope와 새 scope의 완전한 참조를 함께 보존해야 한다."""

    east = _bundle("east-poc-v2")
    extra_members = normalize_scope_members(
        [
            {
                "fact_id": "r-hotel-place-market",
                "region_code": "JEJU_R_HOTEL",
                "grid_id": "BUS_CANDIDATE_V1",
                "member_type": "PLACE",
                "member_id": "tourapi.place:992309",
                "role": "candidate_visit",
                "required": "true",
                "source_reference": "https://www.data.go.kr/data/15101578/openapi.do",
            }
        ]
    )
    extra_steps = normalize_itinerary_template_steps(
        [
            {
                "fact_id": "r-hotel-balanced-visit",
                "region_code": "JEJU_R_HOTEL",
                "grid_id": "BUS_CANDIDATE_V1",
                "strategy": "balanced",
                "sequence": "1",
                "activity_type": "visit",
                "candidate_group": "r-hotel-visit",
                "required": "true",
                "source_reference": "internal://policy/r-hotel-bus-candidate-v1",
            }
        ]
    )
    extra_candidates = normalize_itinerary_template_candidates(
        [
            {
                "fact_id": "r-hotel-candidate-market",
                "region_code": "JEJU_R_HOTEL",
                "grid_id": "BUS_CANDIDATE_V1",
                "candidate_group": "r-hotel-visit",
                "place_fact_id": "tourapi.place:992309",
                "priority": "1",
                "source_reference": "https://www.data.go.kr/data/15101578/openapi.do",
            }
        ]
    )
    assert not (*extra_members.rejections, *extra_steps.rejections, *extra_candidates.rejections)

    validate_scope_manifest_bundle(
        ScopeManifestBundle(
            (*east.members, *extra_members.records),
            (*east.steps, *extra_steps.records),
            (*east.candidates, *extra_candidates.records),
        )
    )


def test_service_scopes_v3_preserves_east_and_adds_r_hotel_hours_scope() -> None:
    """V3 manifest는 동부 구성원을 잃지 않고 제주알호텔 운영시간 분모 네 곳을 추가해야 한다."""

    previous = _bundle("east-poc-v2")
    combined = _bundle("service-scopes-v3")
    validate_scope_manifest_bundle(combined)

    previous_members = {
        (item.region_code, item.grid_id, item.member_type, item.member_id, item.role)
        for item in previous.members
    }
    combined_members = {
        (item.region_code, item.grid_id, item.member_type, item.member_id, item.role)
        for item in combined.members
    }
    assert previous_members <= combined_members
    r_hotel_required = {
        item.member_id
        for item in combined.members
        if item.region_code == "JEJU_R_HOTEL"
        and item.grid_id == "BUS_CANDIDATE_V1"
        and item.member_type == "PLACE"
        and item.required
        and item.role != "accommodation"
    }
    assert r_hotel_required == {
        "tourapi.place:992309",
        "tourapi.place:129384",
        "tourapi.place:1872476",
        "tourapi.place:2853982",
    }


def test_current_opening_hours_cover_every_required_activity_place() -> None:
    """현재 운영시간 묶음은 숙소를 제외한 필수 활동 장소 전체를 정확한 날짜 범위로 덮어야 한다."""

    scope = _bundle("east-poc-v2")
    directory = ROOT / "docs" / "manifests" / "east-poc-hours-2026-08-24"
    rules = normalize_place_opening_rules(_rows(directory / "place-opening-rules.csv"))
    exceptions = normalize_place_schedule_exceptions(
        _rows(directory / "place-schedule-exceptions.csv")
    )

    assert not (*rules.rejections, *exceptions.rejections)
    assert not exceptions.records
    required_places = {
        member.member_id
        for member in scope.members
        if member.member_type == "PLACE" and member.required and member.role != "accommodation"
    }
    assert {rule.place_fact_id for rule in rules.records} == required_places
    assert {rule.valid_from for rule in rules.records} == {date(2026, 8, 24)}
    assert {rule.valid_to for rule in rules.records} == {date(2026, 8, 30)}
