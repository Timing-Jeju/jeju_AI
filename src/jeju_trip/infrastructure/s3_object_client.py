"""MinIO/S3 private raw bucket의 조건부 object client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from jeju_trip.infrastructure.raw_store import ObjectHead


class S3ObjectClient:
    def __init__(self, client: Any) -> None:
        self._client = client

    def put_if_absent(
        self,
        bucket: str,
        key: str,
        path: Path,
        checksum: str,
        content_type: str,
    ) -> None:
        try:
            with path.open("rb") as body:
                self._client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    Metadata={"sha256": checksum},
                    IfNoneMatch="*",
                )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "409",
                "412",
            }:
                raise

    def head(self, bucket: str, key: str) -> ObjectHead:
        response = self._client.head_object(Bucket=bucket, Key=key)
        metadata = response.get("Metadata") or {}
        return ObjectHead(
            checksum=str(metadata.get("sha256") or ""),
            byte_length=int(response.get("ContentLength") or 0),
            version_id=str(response.get("VersionId") or ""),
        )
