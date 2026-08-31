"""private MinIO의 불완전 TourAPI 상세 snapshot을 staging 집계로 출력한다."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import boto3
import psycopg

from jeju_trip.application.partial_snapshot_analysis import (
    PartialTourIntroAnalysis,
    analyze_partial_tour_intro_archive,
)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"STAGING_ENV_MISSING:{name}")
    return value


def _latest_incomplete_pointer() -> tuple[str, str]:
    with psycopg.connect(_required("JEJU_IMPORTER_DSN")) as connection:
        row = connection.execute(
            """
            SELECT raw.bucket, raw.object_key
            FROM source_admin.acquisition acquisition
            JOIN source_admin.raw_object raw
              ON raw.source_id = acquisition.source_id
             AND raw.checksum = acquisition.raw_checksum
            WHERE acquisition.source_id = 'tourapi.place-intro'
              AND acquisition.status = 'INCOMPLETE'
            ORDER BY acquisition.observed_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise ValueError("STAGING_PARTIAL_SNAPSHOT_MISSING")
    return str(row[0]), str(row[1])


def _read_private_raw(bucket: str, object_key: str) -> bytes:
    client = boto3.client(
        "s3",
        endpoint_url=_required("JEJU_RAW_S3_ENDPOINT"),
        aws_access_key_id=_required("JEJU_RAW_S3_ACCESS_KEY"),
        aws_secret_access_key=_required("JEJU_RAW_S3_SECRET_KEY"),
        region_name=os.getenv("JEJU_RAW_S3_REGION", "ap-northeast-2"),
    )
    return client.get_object(Bucket=bucket, Key=object_key)["Body"].read()


def _markdown(result: PartialTourIntroAnalysis, generated_at: datetime) -> str:
    content_types = "\n".join(
        f"- 콘텐츠 유형 `{key}`: {value}건" for key, value in result.content_type_counts.items()
    )
    rejection_reasons = "\n".join(
        f"- `{key}`: {value}건" for key, value in result.rejection_reason_counts.items()
    )
    parseable_content_types = "\n".join(
        f"- 콘텐츠 유형 `{key}`: {value}곳"
        for key, value in result.parseable_place_content_type_counts.items()
    )
    rejection_content_types = "\n".join(
        f"- `{key}`: {value}건"
        for key, value in result.rejection_content_type_reason_counts.items()
    )
    unverified_shapes = "\n".join(
        f"- `{key}`: {value}건" for key, value in result.unverified_shape_counts.items()
    )
    return f"""# TourAPI 부분 snapshot staging 분석

생성시각: {generated_at.isoformat()}

## 판정

- 상태: `{result.status}`
- 전체 조회 대상: {result.query_scope}건
- 완료 조회: {result.completed_queries}건
- 누락 조회: {result.missing_queries}건
- 부분 완성률: {result.completion_ratio:.2%}
- 운영 활성화 허용: `false`

## 운영시간 정규화

- 원본 행: {result.raw_rows}건
- 운영시간 텍스트 보유: {result.rows_with_hours}건
- 문자열 구조화 가능 장소: {result.parseable_places}곳
- 생성된 요일별 규칙: {result.normalized_rules}건
- 거부 행: {result.rejected_rows}건

## 콘텐츠 유형

{content_types}

## 구조화 가능 콘텐츠 유형

{parseable_content_types}

## 거부 사유

{rejection_reasons}

## 콘텐츠 유형별 거부 사유

{rejection_content_types}

## 검증 불가 형식 분류

{unverified_shapes}

## 사용 조건

이 결과는 staging 분석과 비운영 PoC에만 사용한다. `opening_hours_ready`를 활성화하거나
제주 전역의 완전한 운영시간 publication으로 취급해서는 안 된다. 원문 운영시간과 장소 ID는
이 보고서에 복사하지 않았다.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    bucket, object_key = _latest_incomplete_pointer()
    result = analyze_partial_tour_intro_archive(_read_private_raw(bucket, object_key))
    generated_at = datetime.now(UTC)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(result), "generated_at": generated_at.isoformat()}
    (arguments.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (arguments.output_dir / "report.md").write_text(
        _markdown(result, generated_at), encoding="utf-8"
    )
    print(
        f"상태: Pass\n부분 완성률: {result.completion_ratio:.2%}\n"
        f"문자열 구조화 가능 장소: {result.parseable_places}\n운영 활성화: false"
    )


if __name__ == "__main__":
    main()
