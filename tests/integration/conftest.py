"""live 통합 테스트가 개발 DB를 오염시키지 않도록 실행 대상을 제한한다."""

from __future__ import annotations

import os
import time
from pathlib import Path

import boto3
import psycopg
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from psycopg.conninfo import conninfo_to_dict

from jeju_trip.infrastructure.migrations import run_migrations
from jeju_trip.interfaces.cli import _ensure_versioned_raw_bucket

ROOT = Path(__file__).resolve().parents[2]


def _wait_for_postgres(dsn: str) -> None:
    """막 시작한 test PostGIS가 연결을 받을 때까지만 짧게 기다린다."""

    for attempt in range(120):
        try:
            with psycopg.connect(dsn):
                return
        except psycopg.OperationalError:
            if attempt == 119:
                raise
            time.sleep(0.25)


def _prepare_test_bucket(client) -> None:
    """막 시작한 test MinIO가 준비되면 versioned raw bucket을 만든다."""

    for attempt in range(120):
        try:
            _ensure_versioned_raw_bucket(client)
            return
        except (ClientError, EndpointConnectionError):
            if attempt == 119:
                raise
            time.sleep(0.25)


def pytest_sessionstart(session: pytest.Session) -> None:
    """명시적인 테스트 DB가 아니면 live 통합 테스트 시작을 거부한다."""

    dsn = os.getenv("JEJU_TEST_ADMIN_DSN")
    if dsn and conninfo_to_dict(dsn).get("dbname") != "jeju_trip_test":
        raise pytest.UsageError("JEJU_TEST_ADMIN_DSN_MUST_TARGET_jeju_trip_test")

    endpoint = os.getenv("JEJU_TEST_S3_ENDPOINT")
    if endpoint and endpoint.rstrip("/") != "http://127.0.0.1:59010":
        raise pytest.UsageError("JEJU_TEST_S3_ENDPOINT_MUST_TARGET_EPHEMERAL_TEST_MINIO")

    if dsn:
        _wait_for_postgres(dsn)
        run_migrations(dsn, ROOT / "db" / "migrations")
    s3_names = (
        "JEJU_TEST_S3_ENDPOINT",
        "JEJU_TEST_S3_ACCESS_KEY",
        "JEJU_TEST_S3_SECRET_KEY",
    )
    if all(os.getenv(name) for name in s3_names):
        client = boto3.client(
            "s3",
            endpoint_url=os.environ["JEJU_TEST_S3_ENDPOINT"],
            aws_access_key_id=os.environ["JEJU_TEST_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["JEJU_TEST_S3_SECRET_KEY"],
            region_name="ap-northeast-2",
        )
        _prepare_test_bucket(client)
