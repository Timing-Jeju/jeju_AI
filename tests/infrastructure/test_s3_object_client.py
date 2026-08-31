"""MinIO/S3 object client의 조건부 업로드 테스트."""

from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError

from jeju_trip.infrastructure.s3_object_client import S3ObjectClient


class FakeS3Client:
    def __init__(self, conflict: bool = False) -> None:
        self.conflict = conflict
        self.put_calls: list[dict[str, object]] = []

    def put_object(self, **arguments):
        self.put_calls.append(arguments)
        if self.conflict:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "exists"}},
                "PutObject",
            )
        return {"VersionId": "v1"}

    def head_object(self, **arguments):
        return {
            "Metadata": {"sha256": "a" * 64},
            "ContentLength": 4,
            "VersionId": "v1",
        }


def test_s3_upload_uses_if_none_match_and_sha256_metadata(tmp_path: Path) -> None:
    """S3 원본 업로드는 If-None-Match와 SHA-256 metadata를 함께 전송해야 한다."""

    path = tmp_path / "raw.json"
    path.write_bytes(b"data")
    fake = FakeS3Client()
    client = S3ObjectClient(fake)
    client.put_if_absent("bucket", "key", path, "a" * 64, "application/json")
    arguments = fake.put_calls[0]
    assert arguments["IfNoneMatch"] == "*"
    assert arguments["Metadata"] == {"sha256": "a" * 64}


def test_s3_existing_content_addressed_object_is_reused(tmp_path: Path) -> None:
    """동일 key의 객체가 이미 있으면 409·412를 실패가 아닌 재사용으로 처리해야 한다."""

    path = tmp_path / "raw.json"
    path.write_bytes(b"data")
    client = S3ObjectClient(FakeS3Client(conflict=True))
    client.put_if_absent("bucket", "key", path, "a" * 64, "application/json")


def test_s3_head_reads_checksum_length_and_version() -> None:
    """S3 HEAD 결과에서 checksum·길이·version ID를 검증 포인터로 반환해야 한다."""

    head = S3ObjectClient(FakeS3Client()).head("bucket", "key")
    assert head.checksum == "a" * 64
    assert head.byte_length == 4
    assert head.version_id == "v1"
