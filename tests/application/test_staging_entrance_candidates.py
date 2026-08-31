"""장소 중심좌표 기반 출입구 후보의 운영 격리 테스트."""

from __future__ import annotations

from jeju_trip.application.staging_entrance_candidates import (
    StagingEntrancePlace,
    build_staging_entrance_candidates,
)


def test_place_centers_remain_unverified_entrance_candidates() -> None:
    """TourAPI 장소 중심점은 검증 입구나 지원 이동수단으로 승격하지 않아야 한다."""

    result = build_staging_entrance_candidates(
        (
            StagingEntrancePlace("tourapi.place:1", "대표 숙소", "32"),
            StagingEntrancePlace("tourapi.place:2", "대표 관광지", "12"),
        )
    ).as_dict()

    assert result["status"] == "staging_entrance_candidates"
    assert result["production_activation_allowed"] is False
    assert all(candidate["verification_status"] == "UNVERIFIED" for candidate in result["items"])
    assert all(candidate["usable_for_routing"] is False for candidate in result["items"])
    assert "latitude" not in repr(result)
    assert "longitude" not in repr(result)
    assert "supported_modes" not in repr(result)
