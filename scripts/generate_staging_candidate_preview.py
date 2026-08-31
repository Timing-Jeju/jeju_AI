"""기존 active 장소와 부분 집계만으로 제주 동부 후보 순서를 생성한다."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import psycopg

from jeju_trip.application.staging_candidate_preview import (
    StagingPlace,
    StrategyName,
    build_staging_candidate_preview,
)

ACCOMMODATION_ID = "tourapi.place:2757210"
REQUIRED_ID = "tourapi.place:126435"
MEAL_ID = "tourapi.place:2833201"
REST_ID = "tourapi.place:2847829"
SECONDARY_REST_ID = "tourapi.place:2830228"
ORDER_IDS: dict[StrategyName, tuple[str, ...]] = {
    "balanced": (
        REQUIRED_ID,
        "tourapi.place:2564158",
        MEAL_ID,
        "tourapi.place:127813",
        "tourapi.place:2742256",
        REST_ID,
    ),
    "relaxed": (
        REQUIRED_ID,
        "tourapi.place:2742256",
        MEAL_ID,
        "tourapi.place:2564158",
        SECONDARY_REST_ID,
        REST_ID,
    ),
    "experience_max": (
        REQUIRED_ID,
        "tourapi.place:127813",
        MEAL_ID,
        "tourapi.place:2705373",
        REST_ID,
    ),
}
PARSEABLE_HOURS_IDS: frozenset[str] = frozenset()
QUERY_MEMBER = re.compile(r"^query-(?P<query>\d{6})-page-\d{6}\.json$")


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"STAGING_ENV_MISSING:{name}")
    return value


def _active_places() -> dict[str, StagingPlace]:
    fact_ids = sorted({ACCOMMODATION_ID, *(item for ids in ORDER_IDS.values() for item in ids)})
    with psycopg.connect(_required("JEJU_RUNTIME_DSN")) as connection:
        rows = connection.execute(
            """SELECT fact_id, name, category FROM travel_read.active_place
               WHERE fact_id = ANY(%s)""",
            (fact_ids,),
        ).fetchall()
    places = {
        str(row[0]): StagingPlace(
            place_fact_id=str(row[0]),
            name=str(row[1]),
            category=str(row[2]),
            activity_type=(
                "meal"
                if str(row[0]) == MEAL_ID
                else "rest"
                if str(row[0]) in {REST_ID, SECONDARY_REST_ID}
                else "visit"
            ),
        )
        for row in rows
    }
    if set(places) != set(fact_ids):
        raise ValueError("STAGING_ACTIVE_PLACE_MISSING")
    return places


def _completed_intro_ids(target_ids: set[str]) -> frozenset[str]:
    with psycopg.connect(_required("JEJU_IMPORTER_DSN")) as connection:
        pointer = connection.execute(
            """
            SELECT raw.bucket, raw.object_key
            FROM source_admin.acquisition acquisition
            JOIN source_admin.raw_object raw
              ON raw.source_id = acquisition.source_id
             AND raw.checksum = acquisition.raw_checksum
            WHERE acquisition.source_id = 'tourapi.place-intro'
              AND acquisition.status = 'INCOMPLETE'
            ORDER BY acquisition.observed_at DESC LIMIT 1
            """
        ).fetchone()
    if pointer is None:
        raise ValueError("STAGING_PARTIAL_SNAPSHOT_MISSING")
    client = boto3.client(
        "s3",
        endpoint_url=_required("JEJU_RAW_S3_ENDPOINT"),
        aws_access_key_id=_required("JEJU_RAW_S3_ACCESS_KEY"),
        aws_secret_access_key=_required("JEJU_RAW_S3_SECRET_KEY"),
        region_name=os.getenv("JEJU_RAW_S3_REGION", "ap-northeast-2"),
    )
    raw = client.get_object(Bucket=str(pointer[0]), Key=str(pointer[1]))["Body"].read()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        completed_queries = {
            int(matched["query"])
            for name in archive.namelist()
            if (matched := QUERY_MEMBER.fullmatch(name)) is not None
        }
    completed_ids = {
        f"tourapi.place:{query['contentId']}"
        for index, query in enumerate(manifest["queries"], 1)
        if index in completed_queries
    }
    return frozenset(target_ids & completed_ids)


def _markdown(payload: dict[str, Any]) -> str:
    orders = "\n".join(
        f"- `{candidate['strategy']}`: "
        + " → ".join(place["name"] for place in candidate["places"])
        for candidate in payload["candidate_orders"]
    )
    unique_places = {
        place["place_fact_id"]: place
        for candidate in payload["candidate_orders"]
        for place in candidate["places"]
    }
    hours = "\n".join(
        f"- {place['name']}: `{place['opening_hours_status']}`" for place in unique_places.values()
    )
    return f"""# 제주 동부 부분 데이터 후보 순서 preview

생성시각: {payload["generated_at"]}

## 판정

- 상태: `{payload["status"]}`
- 운영 추천 여부: `false`
- 부분 상세정보 완성률: {payload["source_completion_ratio"]:.2%}
- 대표 숙소: {payload["accommodation"]["name"]}
- 숙소 endpoint: `provisional_center_only`

## 후보 순서

{orders}

## 운영시간 상세 조회 상태

{hours}

## 아직 없는 근거

{chr(10).join(f"- `{value}`" for value in payload["missing_evidence"])}

## 해석

이미 적재된 TourAPI 장소만으로 세 전략의 화면 형태와 장소 순서 다양성을 먼저 확인한 결과다.
시간·거리·비용·가능 여부를 계산하지 않았고 공개 `recommend_jeju_day_trips` 성공 응답으로
사용할 수 없다. 외부 API를 추가 호출하지 않았으므로 TourAPI와 TMAP 사용량은 0건이다.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--partial-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    summary = json.loads(arguments.partial_summary.read_text(encoding="utf-8"))
    completion_ratio = float(summary["completion_ratio"])
    places = _active_places()
    candidate_place_ids = {
        place_fact_id for fact_ids in ORDER_IDS.values() for place_fact_id in fact_ids
    }
    preview = build_staging_candidate_preview(
        accommodation=places[ACCOMMODATION_ID],
        candidate_orders={
            strategy: tuple(places[fact_id] for fact_id in fact_ids)
            for strategy, fact_ids in ORDER_IDS.items()
        },
        required_place_ids=frozenset({REQUIRED_ID}),
        parseable_hours_ids=PARSEABLE_HOURS_IDS,
        completed_intro_ids=_completed_intro_ids(candidate_place_ids),
        source_completion_ratio=completion_ratio,
    )
    payload = {**preview.as_dict(), "generated_at": datetime.now(UTC).isoformat()}
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    (arguments.output_dir / "candidate-preview.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (arguments.output_dir / "candidate-preview.md").write_text(_markdown(payload), encoding="utf-8")
    print("상태: staging_candidate_preview\n후보 순서: 3\n외부 API 호출: 0")


if __name__ == "__main__":
    main()
