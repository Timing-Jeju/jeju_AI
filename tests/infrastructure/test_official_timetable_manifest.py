"""공식 XLSX와 TAGO route pattern의 유일 매핑 테스트."""

from __future__ import annotations

from jeju_trip.infrastructure.official_timetable_manifest import (
    TimetableRouteStop,
    _major_stop_times,
    _route_signature,
    _row_signature,
)


def _stop(sequence: int, name: str) -> TimetableRouteStop:
    return TimetableRouteStop(
        provider_route_pattern_id="JEB-route-201",
        route_number="201",
        provider_stop_id=f"JEB-stop-{sequence}",
        route_sequence=sequence,
        direction_text="성산 방면",
        latitude=33.4 + sequence / 100,
        longitude=126.8 + sequence / 100,
        stop_name=name,
    )


def test_xlsx_row_signature_matches_route_signature() -> None:
    """XLSX의 endpoint·선택 경유 표시는 동일 TAGO route signature로 변환돼야 한다."""

    route = (
        _stop(1, "제주버스터미널(가상정류소)"),
        _stop(2, "세화고등학교"),
        _stop(3, "성산일출봉입구[동]"),
    )
    headers = (None, "구분", "제주터미널", "세화고(경유)", "성산")
    values = (None, 1, "09:00", "○", "10:00")

    assert _row_signature("201", headers, values, ((2, "0900"), (4, "1000"))) == (
        *_route_signature(route)[:3],
        False,
        True,
        False,
        False,
        False,
        False,
    )


def test_ambiguous_provider_stop_is_not_used_as_exact_stop_time() -> None:
    """같은 이름의 provider 정류장이 둘이면 해당 시각을 추정 매핑하지 않아야 한다."""

    route = (
        _stop(1, "제주버스터미널(가상정류소)"),
        _stop(2, "수산2리 입구"),
        _stop(3, "수산2리 입구"),
        _stop(4, "성산항(종점)"),
    )
    headers = (None, "제주터미널", "수산2리입구", "성산포항")
    values = (None, "09:00", "09:30", "10:00")

    times = _major_stop_times(
        route,
        headers,
        values,
        ((1, "0900"), (2, "0930"), (3, "1000")),
    )

    assert [item["provider_stop_id"] for item in times] == ["JEB-stop-1", "JEB-stop-4"]
    assert [item["route_sequence"] for item in times] == [1, 4]
