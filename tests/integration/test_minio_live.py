"""선택적으로 실행하는 실제 MinIO raw object 통합 테스트."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import boto3
import pytest

from jeju_trip.infrastructure.raw_store import PrivateRawObjectStore
from jeju_trip.infrastructure.s3_object_client import S3ObjectClient
from jeju_trip.infrastructure.source_catalog import SourceCatalog

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = (
    "JEJU_TEST_S3_ENDPOINT",
    "JEJU_TEST_S3_ACCESS_KEY",
    "JEJU_TEST_S3_SECRET_KEY",
)


@pytest.mark.skipif(
    not all(os.getenv(name) for name in REQUIRED),
    reason="로컬 MinIO 접속정보가 제공된 경우에만 실행합니다.",
)
def test_live_minio_uploads_and_heads_content_addressed_raw() -> None:
    """실제 MinIO에 조건부 업로드한 raw object의 checksum·길이·version을 HEAD로 확인해야 한다."""

    boto_client = boto3.client(
        "s3",
        endpoint_url=os.environ["JEJU_TEST_S3_ENDPOINT"],
        aws_access_key_id=os.environ["JEJU_TEST_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["JEJU_TEST_S3_SECRET_KEY"],
        region_name="ap-northeast-2",
    )
    base = SourceCatalog.load(ROOT / "config/data_sources.toml").require(
        "transport.stop-identity-map"
    )
    contract = base.model_copy(update={"id": f"integration.raw-{uuid4().hex}"})
    registered: list[object] = []
    pointer = PrivateRawObjectStore(S3ObjectClient(boto_client)).store_verified(
        contract,
        [b'{"canonical_stop_id":"integration"}\n'],
        "ndjson",
        "application/x-ndjson",
        registered.append,
    )
    assert registered == [pointer]
    assert pointer.object_version_id
    assert pointer.byte_length > 0
