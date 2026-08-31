"""전역 TAGO 정류장 identity 후보의 공식 ID·좌표 검증 테스트."""

import pytest

from scripts.generate_tago_stop_identity_candidates import build_identity_records


def test_active_official_stops_are_mapped_even_without_route_membership() -> None:
    """공식 active 정류장은 route 미참조 상태여도 자체 TAGO ID로 confirmed mapping해야 한다."""

    records, missing_active, active_without_route = build_identity_records(
        (("stop-a", 33.45, 126.91), ("stop-retired", 33.46, 126.92)),
        (
            ("stop-a", "JEB1", "성산정류장[동]", None, 33.45, 126.91),
            ("stop-b", "JEB2", "성산정류장[서]", "서쪽", 33.451, 126.911),
        ),
    )

    assert [record["canonical_stop_id"] for record in records] == [
        "jeju.stop:tago:JEB1",
        "jeju.stop:tago:JEB2",
    ]
    assert records[0]["direction_text"] == "성산정류장[동]"
    assert records[1]["direction_text"] == "서쪽"
    assert records[0]["source_reference"] == (
        "https://www.data.go.kr/data/15098529/openapi.do"
    )
    assert missing_active == ("stop-retired",)
    assert active_without_route == ("stop-b",)


def test_duplicate_provider_stop_id_is_rejected() -> None:
    """서로 다른 active fact가 같은 공식 정류장 ID를 쓰면 전역 발행을 중단해야 한다."""

    with pytest.raises(ValueError, match="STOP_IDENTITY_PROVIDER_STOP_ID_DUPLICATED"):
        build_identity_records(
            (),
            (
                ("stop-a", "JEB1", "정류장 A", None, 33.45, 126.91),
                ("stop-b", "JEB1", "정류장 B", None, 33.46, 126.92),
            ),
        )


def test_route_stop_coordinate_drift_is_rejected() -> None:
    """같은 fact ID의 route-stop 좌표가 active 정류장과 다르면 identity를 확정하지 않아야 한다."""

    with pytest.raises(ValueError, match="STOP_IDENTITY_COORDINATE_MISMATCH"):
        build_identity_records(
            (("stop-a", 33.40, 126.80),),
            (("stop-a", "JEB1", "정류장 A", None, 33.45, 126.91),),
        )
