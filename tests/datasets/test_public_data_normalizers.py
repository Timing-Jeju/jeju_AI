"""TourAPI와 TAGO typed record 정규화 테스트."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from jeju_trip.infrastructure.public_data_normalizers import (
    PlaceOpeningObservationRecord,
    normalize_holidays,
    normalize_place_opening_rules,
    normalize_place_schedule_exceptions,
    normalize_scheduled_stop_times,
    normalize_tago_arrivals,
    normalize_tago_jeju_stops,
    normalize_tago_route_stops,
    normalize_tago_routes,
    normalize_tago_stops,
    normalize_tour_intro_opening_rules,
    normalize_tour_intro_opening_snapshot,
    normalize_tour_intro_weekly_closures,
    normalize_tour_opening_hours,
    normalize_tour_places,
)


def test_tourapi_place_preserves_official_id_and_coordinates() -> None:
    """TourAPI 장소는 contentId와 WGS84 좌표를 typed field로 보존해야 한다."""

    batch = normalize_tour_places(
        [
            {
                "contentid": "126435",
                "contenttypeid": "12",
                "title": "성산일출봉",
                "addr1": "제주특별자치도 서귀포시 성산읍",
                "mapy": "33.4581",
                "mapx": "126.9425",
                "cat1": "A01",
                "cat2": "A0101",
                "cat3": "A01010400",
                "firstimage": "https://example.invalid/image.jpg",
            }
        ]
    )
    assert batch.rejections == ()
    assert batch.records[0].source_record_id == "126435"
    assert batch.records[0].position.latitude == 33.4581
    assert batch.records[0].category_level_3 == "A01010400"
    assert "firstimage" not in batch.records[0].model_dump()


def test_tourapi_place_outside_jeju_is_rejected() -> None:
    """제주 범위 밖 TourAPI 장소는 blocking rejection으로 분리해야 한다."""

    batch = normalize_tour_places(
        [
            {
                "contentid": "seoul-1",
                "contenttypeid": "12",
                "title": "서울 장소",
                "addr1": "서울특별시",
                "mapy": "37.56",
                "mapx": "126.98",
            }
        ]
    )
    assert batch.records == ()
    assert batch.rejections[0].reason_code == "COORDINATE_OUTSIDE_JEJU_PREFILTER"


def test_tago_stop_preserves_provider_id_without_canonical_overwrite() -> None:
    """TAGO 정류장은 provider ID를 보존하고 canonical ID를 임의 생성하지 않아야 한다."""

    batch = normalize_tago_stops(
        [
            {
                "nodeid": "JJB500000001",
                "nodenm": "성산일출봉입구",
                "citycode": "39",
                "gpslati": "33.459",
                "gpslong": "126.936",
            }
        ]
    )
    assert batch.rejections == ()
    assert batch.records[0].provider_stop_id == "JJB500000001"
    assert not hasattr(batch.records[0], "canonical_stop_id")


def test_tago_route_stop_preserves_official_coordinates() -> None:
    """TAGO 경유 정류장 record는 품질 측정과 거리 계산을 위해 공식 좌표를 보존해야 한다."""

    batch = normalize_tago_route_stops(
        [
            {
                "routeid": "JEB405320111",
                "nodeid": "JJB500000001",
                "nodenm": "성산일출봉입구",
                "nodeord": "1",
                "updowncd": "상행",
                "gpslati": "33.459",
                "gpslong": "126.936",
            }
        ]
    )

    assert batch.rejections == ()
    assert batch.records[0].position.latitude == 33.459
    assert batch.records[0].position.longitude == 126.936


def test_tago_jeju_stop_uses_the_verified_profile_city_code() -> None:
    """도시 전체 정류소 응답에 citycode가 없어도 profile의 제주 코드만 명시적으로 결합해야 한다."""

    batch = normalize_tago_jeju_stops(
        [
            {
                "nodeid": "JJB500000002",
                "nodenm": "제주버스터미널",
                "gpslati": "33.499",
                "gpslong": "126.514",
            }
        ]
    )
    assert batch.rejections == ()
    assert batch.records[0].city_code == "39"


def test_tago_route_parses_first_last_bus_and_intervals() -> None:
    """TAGO 노선은 첫차·막차와 요일별 배차간격을 typed 값으로 변환해야 한다."""

    batch = normalize_tago_routes(
        [
            {
                "routeid": "JJB394000101",
                "routeno": "101",
                "routetp": "간선버스",
                "startnodenm": "제주공항",
                "endnodenm": "성산항",
                "startvehicletime": "0600",
                "endvehicletime": "2230",
                "intervaltime": "29",
                "intervalsattime": "31",
                "intervalsuntime": "35",
            }
        ]
    )
    route = batch.records[0]
    assert route.first_departure == time(6, 0)
    assert route.last_departure == time(22, 30)
    assert route.weekday_interval_minutes == 29
    assert route.sunday_interval_minutes == 35


def test_ambiguous_tourapi_opening_hours_remain_unknown() -> None:
    """일출·날씨에 따라 달라지는 운영시간을 임의 시각으로 해석하지 않아야 한다."""

    result = normalize_tour_opening_hours("일출 시부터 일몰 시까지")
    assert result.status == "UNKNOWN"
    assert result.periods == ()


def test_explicit_tourapi_opening_hours_are_structured() -> None:
    """명확한 매일 운영시간과 입장마감은 검증 가능한 period로 구조화해야 한다."""

    result = normalize_tour_opening_hours("매일 09:00~18:00 (입장마감 17:30)")
    assert result.status == "VERIFIED"
    assert result.periods[0].opens_at == time(9, 0)
    assert result.periods[0].closes_at == time(18, 0)
    assert result.periods[0].last_admission_at == time(17, 30)


def test_tourapi_intro_hours_link_rules_to_canonical_place_fact() -> None:
    """TourAPI 상세소개 운영시간은 원 장소 fact에 연결된 요일별 규칙으로 변환해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "123",
                "contenttypeid": "12",
                "usetime": "매일 09:00~18:00 (입장마감 17:30)",
            }
        ]
    )

    assert not batch.rejections
    assert len(batch.records) == 7
    assert all(rule.place_fact_id == "tourapi.place:123" for rule in batch.records)
    assert batch.records[0].last_admission_minute == 17 * 60 + 30


def test_tourapi_intro_ambiguous_hours_are_rejected_not_estimated() -> None:
    """일출·날씨 의존 TourAPI 운영시간은 규칙을 생성하지 않고 검증 불가로 남겨야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [{"contentid": "456", "contenttypeid": "12", "usetime": "일출~일몰"}]
    )

    assert not batch.records
    assert batch.rejections[0].reason_code == "OPENING_HOURS_UNVERIFIED"


def test_tourapi_intro_uses_explicit_annual_open_evidence_for_unlabeled_hours() -> None:
    """TourAPI 휴무 필드가 연중무휴인 경우만 무요일 시간 구간을 매일 규칙으로 변환해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "food-1",
                "contenttypeid": "39",
                "opentimefood": "08:00~20:00<br>라스트 오더 19:30",
                "restdatefood": "연중무휴",
            }
        ]
    )

    assert not batch.rejections
    assert len(batch.records) == 7
    assert {record.service_day for record in batch.records} == set(range(1, 8))
    assert all(record.opens_minute == 8 * 60 for record in batch.records)
    assert all(record.closes_minute == 20 * 60 for record in batch.records)
    assert all(record.last_order_minute == 19 * 60 + 30 for record in batch.records)


def test_tourapi_intro_reads_daily_scope_after_single_clock_range() -> None:
    """단일 시간대 뒤의 명시적 매일 운영 표기도 매일 규칙으로 구조화해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "food-daily-suffix",
                "contenttypeid": "39",
                "opentimefood": "09:00~18:00 (매일 운영)",
            }
        ]
    )

    assert batch.rejections == ()
    assert len(batch.records) == 7
    assert {record.service_day for record in batch.records} == set(range(1, 8))


def test_tourapi_intro_reads_shopping_open_time_field() -> None:
    """쇼핑 콘텐츠의 opentime도 공식 운영시간 필드로 정규화해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "shopping-1",
                "contenttypeid": "38",
                "opentime": "09:00~20:00",
                "restdateshopping": "연중무휴",
            }
        ]
    )

    assert batch.rejections == ()
    assert len(batch.records) == 7
    assert {record.service_day for record in batch.records} == set(range(1, 8))
    assert {record.opens_minute for record in batch.records} == {9 * 60}
    assert {record.closes_minute for record in batch.records} == {20 * 60}


def test_tourapi_intro_structures_explicit_always_available_places() -> None:
    """상시 이용 가능과 연중무휴가 둘 다 명시된 장소만 24시간 규칙으로 변환해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "outdoor-1",
                "contenttypeid": "12",
                "usetime": "상시 이용 가능",
                "restdate": "연중무휴",
            }
        ]
    )

    assert not batch.rejections
    assert len(batch.records) == 7
    assert all(record.opens_minute == 0 for record in batch.records)
    assert all(record.closes_minute == 0 for record in batch.records)
    assert all(record.closes_day_offset == 1 for record in batch.records)


def test_tourapi_intro_structures_explicit_year_round_places() -> None:
    """TourAPI의 연중 이용 가능 표현은 연중무휴 근거와 함께일 때만 24시간 규칙이어야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "outdoor-2",
                "contenttypeid": "12",
                "usetime": "연중 이용 가능",
                "restdate": "연중무휴",
            }
        ]
    )

    assert not batch.rejections
    assert len(batch.records) == 7
    assert all(record.closes_day_offset == 1 for record in batch.records)


def test_tourapi_intro_structures_explicit_always_open_places() -> None:
    """TourAPI의 상시개방 표현은 연중무휴 근거와 함께일 때만 24시간 규칙이어야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "outdoor-3",
                "contenttypeid": "12",
                "usetime": "상시개방",
                "restdate": "연중무휴",
            }
        ]
    )

    assert not batch.rejections
    assert len(batch.records) == 7
    assert all(record.closes_day_offset == 1 for record in batch.records)


def test_tourapi_intro_rejects_multiple_unlabeled_open_ranges() -> None:
    """의미 라벨이 없는 시간 구간이 여러 개이면 임의로 영업·휴게 구간을 나누지 않아야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "food-ambiguous",
                "contenttypeid": "39",
                "opentimefood": "07:30~17:00 / 15:00~15:30",
                "restdatefood": "연중무휴",
            }
        ]
    )

    assert not batch.records
    assert batch.rejections[0].reason_code == "OPENING_HOURS_UNVERIFIED"


def test_tourapi_intro_does_not_assume_days_without_rest_evidence() -> None:
    """휴무 근거가 없는 무요일 운영시간은 매일 규칙으로 추정하지 않아야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [{"contentid": "food-2", "contenttypeid": "39", "opentimefood": "08:00~20:00"}]
    )

    assert not batch.records
    assert batch.rejections[0].reason_code == "OPENING_HOURS_UNVERIFIED"


def test_tourapi_intro_weekend_rest_limits_unlabeled_hours_to_weekdays() -> None:
    """휴무일이 주말이면 무요일 영업시간을 평일 규칙으로만 구조화해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "food-weekday",
                "contenttypeid": "39",
                "opentimefood": "09:00~17:00 (마지막 주문 16:10)",
                "restdatefood": "주말",
            }
        ]
    )

    assert batch.rejections == ()
    assert tuple(rule.service_day for rule in batch.records) == (1, 2, 3, 4, 5)
    assert {rule.last_order_minute for rule in batch.records} == {16 * 60 + 10}


def test_tourapi_intro_weekly_closed_day_limits_service_days() -> None:
    """휴무일이 매주 특정 요일이면 그 요일을 제외한 운영 규칙만 발행해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "food-closed-tuesday",
                "contenttypeid": "39",
                "opentimefood": "11:00~22:00",
                "restdatefood": "매주 화요일",
            }
        ]
    )

    assert batch.rejections == ()
    assert tuple(rule.service_day for rule in batch.records) == (1, 3, 4, 5, 6, 7)


def test_tourapi_intro_multiple_weekly_closed_days_limit_service_days() -> None:
    """휴무일이 매주 여러 요일의 명시적 목록이면 해당 요일을 모두 제외해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "food-closed-monday-tuesday",
                "contenttypeid": "39",
                "opentimefood": "11:00~22:00",
                "restdatefood": "매주 월요일, 화요일 휴무",
            }
        ]
    )

    assert batch.rejections == ()
    assert tuple(rule.service_day for rule in batch.records) == (3, 4, 5, 6, 7)


def test_tourapi_intro_preserves_multiple_weekly_closed_days_as_facts() -> None:
    """TourAPI의 명시적 복수 주간 휴무는 날짜 추정 없이 반복 휴무 fact로 보존해야 한다."""

    batch = normalize_tour_intro_weekly_closures(
        [
            {
                "contentid": "food-closed-monday-tuesday",
                "contenttypeid": "39",
                "restdatefood": "매주 월요일, 화요일 휴무",
            }
        ]
    )

    assert batch.rejections == ()
    assert tuple(record.service_day for record in batch.records) == (1, 2)
    assert all(
        record.place_fact_id == "tourapi.place:food-closed-monday-tuesday"
        for record in batch.records
    )


def test_tourapi_intro_snapshot_preserves_every_completed_query_outcome() -> None:
    """완료된 상세소개 조회는 구조화·모호·빈 결과를 장소별 관측 fact로 하나씩 남겨야 한다."""

    batch = normalize_tour_intro_opening_snapshot(
        [
            {
                "contentid": "structured",
                "contenttypeid": "12",
                "usetime": "매일 09:00~18:00",
            },
            {
                "contentid": "ambiguous",
                "contenttypeid": "12",
                "usetime": "일출~일몰",
            },
        ],
        (
            {"contentId": "structured", "contentTypeId": "12"},
            {"contentId": "ambiguous", "contentTypeId": "12"},
            {"contentId": "empty", "contentTypeId": "39"},
        ),
    )

    observations = tuple(
        record
        for record in batch.records
        if isinstance(record, PlaceOpeningObservationRecord)
    )
    assert len(observations) == 3
    assert tuple(record.observation_status for record in observations) == (
        "STRUCTURED",
        "UNVERIFIABLE",
        "NO_DATA",
    )
    assert tuple(record.reason_code for record in observations) == (
        None,
        "OPENING_HOURS_UNVERIFIED",
        "SOURCE_RESPONSE_EMPTY",
    )


def test_tourapi_intro_snapshot_rejects_rows_outside_the_query_scope() -> None:
    """상세소개 응답 장소가 query manifest에 없으면 완전 스냅샷으로 가장하지 않아야 한다."""

    with pytest.raises(ValueError, match="TOUR_INTRO_RESPONSE_SCOPE_MISMATCH"):
        normalize_tour_intro_opening_snapshot(
            [
                {
                    "contentid": "unexpected",
                    "contenttypeid": "12",
                    "usetime": "매일 09:00~18:00",
                }
            ],
            ({"contentId": "expected", "contentTypeId": "12"},),
        )


def test_tourapi_festival_hours_require_official_event_dates() -> None:
    """행사 운영시간은 공식 시작일과 종료일이 없으면 영구 규칙으로 발행하지 않아야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "festival-without-dates",
                "contenttypeid": "15",
                "usetimefestival": "매일 10:00~18:00",
            }
        ]
    )

    assert not batch.records
    assert batch.rejections[0].reason_code == "EVENT_DATE_RANGE_REQUIRED"


def test_tourapi_festival_hours_keep_official_event_date_range() -> None:
    """행사 운영시간은 공식 행사 기간을 모든 요일 규칙의 유효 범위로 보존해야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [
            {
                "contentid": "festival-with-dates",
                "contenttypeid": "15",
                "usetimefestival": "매일 10:00~18:00",
                "eventstartdate": "20260824",
                "eventenddate": "20260825",
            }
        ]
    )

    assert batch.rejections == ()
    assert {rule.valid_from for rule in batch.records} == {date(2026, 8, 24)}
    assert {rule.valid_to for rule in batch.records} == {date(2026, 8, 25)}


def test_weekday_and_break_hours_are_structured_without_guessing() -> None:
    """요일별 운영시간과 브레이크타임은 각각 명시된 period로 구조화해야 한다."""

    result = normalize_tour_opening_hours(
        "월~금 09:00~18:00 / 토 10:00~17:00 / 브레이크타임 15:00~16:00"
    )
    assert result.status == "VERIFIED"
    assert result.periods[0].service_days == (1, 2, 3, 4, 5)
    assert result.periods[1].service_days == (6,)
    assert result.break_periods[0].opens_at == time(15, 0)


def test_full_korean_weekday_ranges_are_not_misread_as_sunday() -> None:
    """요일 접미사가 있는 범위는 단어 끝의 일을 일요일 하나로 오인하지 않아야 한다."""

    result = normalize_tour_opening_hours(
        "- 화요일~금요일 09:00~19:00<br>- 토요일~월요일 09:00~21:00"
    )

    assert result.status == "VERIFIED"
    assert result.periods[0].service_days == (2, 3, 4, 5)
    assert result.periods[1].service_days == (6, 7, 1)


def test_weekday_and_weekend_labels_are_structured() -> None:
    """평일과 주말 표기는 각각 월~금과 토~일의 명시적 운영 규칙으로 구조화해야 한다."""

    result = normalize_tour_opening_hours("- 평일 11:00~19:00<br>- 주말 11:00~21:00")

    assert result.status == "VERIFIED"
    assert result.periods[0].service_days == (1, 2, 3, 4, 5)
    assert result.periods[1].service_days == (6, 7)


def test_compact_month_range_hours_are_not_promoted() -> None:
    """숫자 월 범위가 반복된 계절 운영시간은 상시 요일 규칙으로 승격하지 않아야 한다."""

    result = normalize_tour_opening_hours(
        "1~2월 06:00~18:00 / 3~4월 05:00~19:00 / 5~8월 04:00~20:00"
    )

    assert result.status == "PARTIAL"


def test_overnight_opening_hours_preserve_day_offset() -> None:
    """자정을 넘는 영업시간은 폐장 시각을 버리지 않고 day offset으로 보존해야 한다."""

    result = normalize_tour_opening_hours("매일 18:00~02:00")
    assert result.status == "VERIFIED"
    assert result.periods[0].closes_day_offset == 1


def test_seasonal_opening_hours_are_not_promoted_to_verified() -> None:
    """계절·일출 기준 운영시간은 적용기간 없이 영구 VERIFIED 규칙이 되지 않아야 한다."""

    result = normalize_tour_opening_hours("하절기 매일 09:00~18:00 / 일몰에 따라 변동")
    assert result.status == "PARTIAL"


def test_month_range_opening_hours_are_not_promoted_to_permanent_rule() -> None:
    """월 범위가 붙은 운영시간은 적용기간 구조화 전까지 영구 규칙으로 발행하지 않아야 한다."""

    result = normalize_tour_opening_hours("3월~10월 매일 09:00~18:00")

    assert result.status == "PARTIAL"


def test_lodging_checkin_is_not_misclassified_as_opening_hours() -> None:
    """숙박 체크인 시각은 관광지 운영시간 규칙으로 변환하지 않아야 한다."""

    batch = normalize_tour_intro_opening_rules(
        [{"contentid": "789", "contenttypeid": "32", "checkintime": "15:00"}]
    )

    assert not batch.records
    assert batch.rejections[0].reason_code == "REQUIRED_FIELD_MISSING"


def test_tago_arrival_preserves_provider_ids_and_seconds() -> None:
    """TAGO 도착정보는 정류장·노선 ID와 예상 초·남은 정류장 수를 보존해야 한다."""

    checked_at = datetime(2026, 8, 15, 12, 20, tzinfo=timezone(timedelta(hours=9)))
    batch = normalize_tago_arrivals(
        [
            {
                "citycode": "39",
                "nodeid": "JJB500000001",
                "routeid": "JJB394000101",
                "routeno": "101",
                "arrtime": "816",
                "arrprevstationcnt": "5",
                "vehicletp": "일반차량",
            }
        ],
        checked_at,
    )
    arrival = batch.records[0]
    assert arrival.arrival_seconds == 816
    assert arrival.remaining_stops == 5
    assert arrival.expected_arrival_at == checked_at + timedelta(seconds=816)


def test_holiday_normalizer_preserves_public_institution_flag() -> None:
    """특일 정보는 날짜·명칭과 공공기관 휴일 여부를 별도 필드로 보존해야 한다."""

    batch = normalize_holidays(
        [{"locdate": "20260815", "dateName": "광복절", "isHoliday": "Y", "dateKind": "01"}]
    )
    assert batch.records[0].holiday_date == date(2026, 8, 15)
    assert batch.records[0].is_public_institution_holiday is True


def test_scheduled_stop_time_supports_after_midnight_values() -> None:
    """공식 시간표의 24시 이후 시각은 다음 날 offset과 시각으로 분리해야 한다."""

    batch = normalize_scheduled_stop_times(
        [
            {
                "trip_id": "trip-1",
                "stop_id": "stop-1",
                "stop_sequence": "1",
                "arrival_time": "25:10",
                "departure_time": "25:12",
            }
        ]
    )
    record = batch.records[0]
    assert record.arrival_at == time(1, 10)
    assert record.arrival_day_offset == 1
    assert record.departure_day_offset == 1


def test_curated_opening_rule_requires_explicit_source_and_valid_range() -> None:
    """수동 운영시간은 공식 원문 참조와 유효한 요일·시각 범위가 있어야 한다."""

    batch = normalize_place_opening_rules(
        [
            {
                "place_fact_id": "tourapi.place:1",
                "rule_id": "place-1-mon",
                "service_day": "1",
                "valid_from": "2026-01-01",
                "valid_to": "2026-12-31",
                "period_kind": "OPEN",
                "opens_at": "09:00",
                "closes_at": "18:00",
                "closes_day_offset": "0",
                "last_admission_at": "17:30",
                "last_order_at": "",
                "source_reference": "https://official.example/place-1",
            }
        ]
    )
    assert batch.rejections == ()
    assert batch.records[0].opens_minute == 540
    assert batch.records[0].last_admission_minute == 1050


def test_closed_exception_rejects_conflicting_hours() -> None:
    """휴무 예외에 운영시각이 함께 적히면 상충된 주장으로 거부해야 한다."""

    batch = normalize_place_schedule_exceptions(
        [
            {
                "place_fact_id": "tourapi.place:1",
                "exception_id": "closed-1",
                "exception_date": "2026-08-15",
                "exception_type": "CLOSED",
                "opens_at": "09:00",
                "closes_at": "",
                "closes_day_offset": "0",
                "source_reference": "https://official.example/place-1",
            }
        ]
    )
    assert batch.records == ()
    assert batch.rejections[0].reason_code == "CLOSED_HOURS_FORBIDDEN"
