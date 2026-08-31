"""정류장 canonical mapping 판정 테스트."""

from __future__ import annotations

from jeju_trip.planning.stop_identity import StopIdentityCandidate, decide_stop_identity


def test_name_only_cannot_confirm_opposite_bus_stop() -> None:
    """이름만 같은 맞은편 정류장을 동일 정류장으로 확정하지 않아야 한다."""

    decision = decide_stop_identity(
        StopIdentityCandidate(
            canonical_stop_id="canonical-1",
            provider="TMAP",
            provider_stop_id="provider-1",
            normalized_name="함덕환승정류장",
            coordinate_distance_meters=None,
            route_sequence_matches=False,
            official_id_matches=False,
        )
    )
    assert decision.status == "REVIEW_REQUIRED"


def test_route_sequence_and_near_coordinate_can_confirm_stop() -> None:
    """노선 순서와 가까운 좌표가 함께 일치하면 정류장 mapping을 확정할 수 있어야 한다."""

    decision = decide_stop_identity(
        StopIdentityCandidate(
            canonical_stop_id="canonical-1",
            provider="TAGO",
            provider_stop_id="provider-1",
            normalized_name="함덕환승정류장",
            coordinate_distance_meters=9,
            route_sequence_matches=True,
            official_id_matches=False,
        )
    )
    assert decision.status == "CONFIRMED"
