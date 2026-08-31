"""수동 검증 데이터 입력 계약 테스트."""

from __future__ import annotations

from jeju_trip.infrastructure.public_data_normalizers import (
    normalize_jeju_boundary,
    normalize_place_entrances,
    normalize_restaurant_dietary_facts,
    normalize_service_calendars,
    normalize_stop_identities,
)


def test_verified_entrance_requires_expiry_modes_and_jeju_position() -> None:
    """입구는 검증 만료와 지원수단 및 제주 좌표가 명시돼야 승인되어야 한다."""

    result = normalize_place_entrances(
        [
            {
                "place_fact_id": "tourapi.place:1",
                "entrance_id": "entrance-1",
                "entrance_type": "accessible",
                "latitude": "33.50",
                "longitude": "126.50",
                "verification_method": "CURATED",
                "verified_at": "2026-08-10T10:00:00+09:00",
                "expires_at": "2027-08-10T10:00:00+09:00",
                "supported_modes": "walk,taxi",
                "source_reference": "internal://review/official-map-record-1",
            }
        ]
    )
    assert len(result.records) == 1
    assert result.records[0].supported_modes == ("walk", "taxi")


def test_verified_entrance_rejects_untraceable_source_reference() -> None:
    """검증 입구는 공식 HTTPS 또는 내부 승인 reference 없이 승격되지 않아야 한다."""

    result = normalize_place_entrances(
        [
            {
                "place_fact_id": "tourapi.place:1",
                "entrance_id": "entrance-1",
                "entrance_type": "main",
                "latitude": "33.50",
                "longitude": "126.50",
                "verification_method": "CURATED",
                "verified_at": "2026-08-10T10:00:00+09:00",
                "expires_at": "2027-08-10T10:00:00+09:00",
                "supported_modes": "walk",
                "source_reference": "untraceable-note",
            }
        ]
    )

    assert result.records == ()
    assert result.rejections[0].reason_code == "SOURCE_REFERENCE_INVALID"


def test_restaurant_dietary_fact_requires_explicit_verified_absence_lists() -> None:
    """식당 메뉴 fact는 안전하다고 검증한 알레르겐·제외음식 목록을 명시적으로 보존해야 한다."""

    result = normalize_restaurant_dietary_facts(
        [
            {
                "dietary_fact_id": "menu-1",
                "place_fact_id": "tourapi.place:1",
                "menu_item_id": "official-menu-1",
                "menu_item_name": "검증 메뉴",
                "verified_free_from_allergens": "땅콩|우유",
                "verified_excludes_foods": "돼지고기",
                "verification_method": "OFFICIAL",
                "verified_at": "2026-08-30T10:00:00+09:00",
                "expires_at": "2026-09-30T10:00:00+09:00",
                "source_reference": "https://official.example/menu/1",
            }
        ]
    )

    assert result.rejections == ()
    assert result.records[0].verified_free_from_allergens == ("땅콩", "우유")
    assert result.records[0].verified_excludes_foods == ("돼지고기",)


def test_restaurant_dietary_fact_rejects_empty_safety_assertion() -> None:
    """안전 부재 항목이 하나도 없는 메뉴 행은 식이 capability 근거로 승인하지 않아야 한다."""

    result = normalize_restaurant_dietary_facts(
        [
            {
                "dietary_fact_id": "menu-empty",
                "place_fact_id": "tourapi.place:1",
                "menu_item_id": "official-menu-empty",
                "menu_item_name": "미검증 메뉴",
                "verified_free_from_allergens": "",
                "verified_excludes_foods": "",
                "verification_method": "CURATED",
                "verified_at": "2026-08-30T10:00:00+09:00",
                "expires_at": "2026-09-30T10:00:00+09:00",
                "source_reference": "internal://dietary-review/menu-empty",
            }
        ]
    )

    assert result.records == ()
    assert result.rejections[0].reason_code == "DIETARY_ASSERTION_EMPTY"


def test_confirmed_stop_identity_needs_direction_and_confidence() -> None:
    """정류장 mapping은 방향이나 confidence가 빠지면 CONFIRMED record가 되지 않아야 한다."""

    result = normalize_stop_identities(
        [
            {
                "canonical_stop_id": "stop-1",
                "provider": "TAGO",
                "provider_stop_id": "JJB1",
                "source_fact_id": "tago.bus-stop:JJB1",
                "latitude": "33.50",
                "longitude": "126.50",
                "normalized_name": "제주 정류장",
                "direction_text": "성산 방면",
                "mapping_method": "ROUTE_SEQUENCE",
                "mapping_confidence": "1.0",
                "mapping_status": "CONFIRMED",
            }
        ]
    )
    assert result.records[0].mapping_status == "CONFIRMED"


def test_service_calendar_rejects_reversed_effective_range() -> None:
    """공식 시간표 service calendar는 적용 종료일이 시작일보다 빠를 수 없어야 한다."""

    result = normalize_service_calendars(
        [
            {
                "service_id": "holiday-2026",
                "day_type": "HOLIDAY",
                "starts_on": "2026-12-31",
                "ends_on": "2026-01-01",
            }
        ]
    )
    assert result.records == ()
    assert result.rejections[0].reason_code == "DATE_RANGE_INVALID"


def test_jeju_boundary_rejects_coordinates_outside_service_area() -> None:
    """제주 경계 projection은 명백히 다른 지역의 polygon을 수용하지 않아야 한다."""

    result = normalize_jeju_boundary(
        {
            "boundary_id": "wrong",
            "name": "다른 지역",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[127.8, 37.5], [127.9, 37.5], [127.9, 37.6], [127.8, 37.5]]],
            },
            "source_reference": "https://official.example/boundary",
        }
    )
    assert result.records == ()
    assert result.rejections[0].reason_code == "BOUNDARY_OUTSIDE_JEJU"


def test_jeju_boundary_rejects_non_jeju_name_inside_broad_box() -> None:
    """좌표가 넓은 사전 범위 안이어도 제주 경계라는 식별 근거가 없으면 거부해야 한다."""

    result = normalize_jeju_boundary(
        {
            "boundary_id": "wrong",
            "name": "다른 행정구역",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[126.1, 33.1], [126.2, 33.1], [126.2, 33.2], [126.1, 33.1]]],
            },
            "source_reference": "https://official.example/boundary",
        }
    )

    assert not result.records
    assert result.rejections[0].reason_code == "BOUNDARY_NAME_NOT_JEJU"
