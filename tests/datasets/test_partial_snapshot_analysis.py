"""불완전 TourAPI snapshot의 staging 분석 경계 테스트."""

from __future__ import annotations

import io
import json
import zipfile

from jeju_trip.application.partial_snapshot_analysis import analyze_partial_tour_intro_archive


def _partial_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": "1",
                    "source_id": "tourapi.place-intro",
                    "query_count": 2,
                    "queries": [
                        {"contentId": "1", "contentTypeId": "12"},
                        {"contentId": "2", "contentTypeId": "12"},
                    ],
                }
            ),
        )
        archive.writestr(
            "query-000001-page-000001.json",
            json.dumps(
                {
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "OK"},
                        "body": {
                            "pageNo": 1,
                            "numOfRows": 10,
                            "totalCount": 8,
                            "items": {
                                "item": [
                                    {
                                        "contentid": "1",
                                        "contenttypeid": "12",
                                        "usetime": "매일 09:00~18:00",
                                    },
                                    {
                                        "contentid": "2",
                                        "contenttypeid": "12",
                                        "usetime": "하절기 09:00~18:00",
                                    },
                                    {
                                        "contentid": "3",
                                        "contenttypeid": "39",
                                        "opentimefood": "09:00~18:00 / 19:00~21:00",
                                        "restdatefood": "연중무휴",
                                    },
                                    {
                                        "contentid": "4",
                                        "contenttypeid": "39",
                                        "opentimefood": "09:00~18:00",
                                    },
                                    {
                                        "contentid": "5",
                                        "contenttypeid": "39",
                                        "opentimefood": "오전 9시~오후 6시",
                                        "restdatefood": "연중무휴",
                                    },
                                    {
                                        "contentid": "6",
                                        "contenttypeid": "12",
                                        "usetime": "현장 문의",
                                    },
                                    {
                                        "contentid": "7",
                                        "contenttypeid": "39",
                                        "opentimefood": "09:00~18:00",
                                        "restdatefood": "매월 첫째 월요일",
                                    },
                                    {
                                        "contentid": "8",
                                        "contenttypeid": "39",
                                        "opentimefood": "09:00~18:00",
                                        "restdatefood": "매주 월요일, 화요일 휴무",
                                    },
                                ]
                            },
                        },
                    }
                }
            ),
        )
    return buffer.getvalue()


def test_partial_snapshot_is_measured_without_becoming_complete() -> None:
    """일부 성공 페이지는 staging 지표로 쓰되 완전 snapshot으로 승격하지 않아야 한다."""

    result = analyze_partial_tour_intro_archive(_partial_archive())

    assert result.status == "staging_partial_only"
    assert result.query_scope == 2
    assert result.completed_queries == 1
    assert result.missing_queries == 1
    assert result.raw_rows == 8
    assert result.parseable_places == 2
    assert result.parseable_place_content_type_counts == {"12": 1, "39": 1}
    assert result.rejection_content_type_reason_counts == {
        "12:OPENING_HOURS_UNVERIFIED": 2,
        "39:OPENING_HOURS_UNVERIFIED": 4,
    }
    assert result.unverified_shape_counts == {
        "CLOCK_RANGE_DAY_SCOPE_MISSING": 1,
        "CLOCK_RANGE_REST_SCOPE_MONTHLY": 1,
        "MULTIPLE_CLOCK_RANGES": 1,
        "NO_EXACT_CLOCK_RANGE": 1,
        "NON_COLON_CLOCK_FORMAT": 1,
        "SEASONAL_OR_VARIABLE": 1,
    }
    assert result.normalized_rules == 12
    assert result.production_activation_allowed is False
