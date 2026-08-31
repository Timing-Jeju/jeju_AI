"""TourAPI 상세정보 한 건을 incomplete staging 보충 raw로 수집한다."""

from __future__ import annotations

import argparse
import io
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import httpx
import psycopg

from jeju_trip.application.refresh_service import PublicDataRefreshService
from jeju_trip.infrastructure.public_data_http import (
    BoundedPublicDataClient,
    parse_public_data_page,
)
from jeju_trip.infrastructure.public_data_normalizers import (
    normalize_tour_intro_opening_rules,
)
from jeju_trip.infrastructure.raw_store import PrivateRawObjectStore
from jeju_trip.infrastructure.s3_object_client import S3ObjectClient
from jeju_trip.infrastructure.source_admin_repository import PostgresSourceAdminRepository
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[1]
CONTENT_ID = "2705373"
CONTENT_TYPE_ID = "28"


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"STAGING_ENV_MISSING:{name}")
    return value


class CapturingRawStore:
    """등록 포인터만 메모리에 보존하고 실제 raw-first 저장소에 위임한다."""

    def __init__(self, delegate: PrivateRawObjectStore) -> None:
        self._delegate = delegate
        self.pointer: Any | None = None

    def store_verified(self, contract, chunks, extension, content_type, register_acquisition):
        def capture(pointer) -> None:
            self.pointer = pointer
            register_acquisition(pointer)

        return self._delegate.store_verified(contract, chunks, extension, content_type, capture)


def _active_name() -> str:
    with psycopg.connect(_required("JEJU_RUNTIME_DSN")) as connection:
        row = connection.execute(
            "SELECT name FROM travel_read.active_place WHERE fact_id = %s",
            (f"tourapi.place:{CONTENT_ID}",),
        ).fetchone()
    if row is None:
        raise ValueError("STAGING_TARGET_PLACE_MISSING")
    return str(row[0])


def _analyze_archive(raw_archive: bytes) -> tuple[int, str, tuple[str, ...]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(raw_archive)) as archive:
        for name in archive.namelist():
            if name != "manifest.json":
                rows.extend(parse_public_data_page(archive.read(name)).items)
    normalized = normalize_tour_intro_opening_rules(rows)
    status = "parseable" if normalized.records else "unparseable"
    reasons = tuple(sorted({item.reason_code for item in normalized.rejections}))
    return len(rows), status, reasons


def _markdown(payload: dict[str, Any]) -> str:
    reasons = ", ".join(f"`{value}`" for value in payload["reason_codes"]) or "없음"
    return f"""# TourAPI 단건 staging 보충 결과

생성시각: {payload["generated_at"]}

- 장소: {payload["place_name"]}
- 장소 fact: `{payload["place_fact_id"]}`
- 수집 상태: `{payload["status"]}`
- 원본 행: {payload["raw_rows"]}건
- 운영시간 해석: `{payload["opening_hours_status"]}`
- 해석 사유: {reasons}
- acquisition 상태: `INCOMPLETE`
- 운영 활성화 허용: `false`
- 외부 호출 범위: TourAPI 상세정보 1개 query, 오류 시 1회 재시도

이 보충 raw는 전체 1,019건 snapshot을 대체하거나 `opening_hours_ready`를 활성화하지 않는다.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    catalog = SourceCatalog.load(ROOT / "config/data_sources.toml")
    contract = catalog.require("tourapi.place-intro")
    environment = {"JEJU_TOURAPI_SERVICE_KEY": _required("JEJU_TOURAPI_SERVICE_KEY")}
    s3 = boto3.client(
        "s3",
        endpoint_url=_required("JEJU_RAW_S3_ENDPOINT"),
        aws_access_key_id=_required("JEJU_RAW_S3_ACCESS_KEY"),
        aws_secret_access_key=_required("JEJU_RAW_S3_SECRET_KEY"),
        region_name=os.getenv("JEJU_RAW_S3_REGION", "ap-northeast-2"),
    )
    raw_store = CapturingRawStore(PrivateRawObjectStore(S3ObjectClient(s3)))
    with httpx.Client(timeout=15) as client:
        outcome = PublicDataRefreshService(
            BoundedPublicDataClient(client),
            raw_store,
            PostgresSourceAdminRepository(
                _required("JEJU_IMPORTER_DSN"), assume_role="jeju_importer"
            ),
        ).refresh_batch(
            contract,
            "detailIntro2",
            (
                {
                    "MobileOS": "ETC",
                    "MobileApp": "jeju-day-trip-planner",
                    "contentId": CONTENT_ID,
                    "contentTypeId": CONTENT_TYPE_ID,
                },
            ),
            environment,
            normalize_tour_intro_opening_rules,
            rows_per_page=10,
            incomplete_reason_code="STAGING_SUPPLEMENTAL_QUERY",
        )
    if raw_store.pointer is None:
        payload = {
            "status": "staging_supplement_unavailable",
            "place_fact_id": f"tourapi.place:{CONTENT_ID}",
            "place_name": _active_name(),
            "reason_code": outcome.reason_code or "PUBLIC_DATA_NO_PAGES",
            "raw_persisted": False,
            "production_activation_allowed": False,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        arguments.output_dir.mkdir(parents=True, exist_ok=True)
        (arguments.output_dir / "tourapi-supplement.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (arguments.output_dir / "tourapi-supplement.md").write_text(
            f"""# TourAPI 단건 staging 보충 결과

- 장소: {payload["place_name"]}
- 상태: `{payload["status"]}`
- 실패 코드: `{payload["reason_code"]}`
- raw 저장: `false`
- 운영 활성화 허용: `false`

응답 페이지를 얻지 못해 raw acquisition을 만들지 않았다. 같은 실행에서 허용된 1회 재시도까지
끝났으므로 추가 호출하지 않는다.
""",
            encoding="utf-8",
        )
        print(
            f"상태: {payload['status']}\n실패 코드: {payload['reason_code']}\n"
            "raw 저장: false\n운영 활성화: false"
        )
        return
    raw_archive = s3.get_object(Bucket=raw_store.pointer.bucket, Key=raw_store.pointer.object_key)[
        "Body"
    ].read()
    raw_rows, hours_status, reasons = _analyze_archive(raw_archive)
    payload = {
        "status": "staging_supplement_fetched",
        "place_fact_id": f"tourapi.place:{CONTENT_ID}",
        "place_name": _active_name(),
        "raw_rows": raw_rows,
        "opening_hours_status": hours_status,
        "reason_codes": reasons,
        "acquisition_status": outcome.status,
        "production_activation_allowed": False,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    (arguments.output_dir / "tourapi-supplement.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (arguments.output_dir / "tourapi-supplement.md").write_text(
        _markdown(payload), encoding="utf-8"
    )
    print(
        f"상태: {payload['status']}\n장소: {payload['place_name']}\n"
        f"운영시간 해석: {hours_status}\n운영 활성화: false"
    )


if __name__ == "__main__":
    main()
