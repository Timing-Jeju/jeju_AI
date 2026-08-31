"""승인된 외부 소스와 수집 제한을 검증한다."""

from __future__ import annotations

import hashlib
import json
import posixpath
import tomllib
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

KST = ZoneInfo("Asia/Seoul")
DEFAULT_SOURCE_CATALOG_PATH = Path(__file__).resolve().parents[3] / "config/data_sources.toml"


class SourceNotApprovedError(ValueError):
    """네트워크 전 source contract 승인이 확인되지 않은 경우."""


class SourceUrlRejectedError(ValueError):
    """요청 URL이 source allowlist 밖인 경우."""


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AcquisitionContract(CatalogModel):
    mode: Literal["API", "FILE", "MANUAL", "ON_DEMAND"]
    base_url: str
    allowed_hosts: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]
    format: Literal["JSON", "XML", "CSV", "NDJSON", "TOML", "ZIP"]
    encoding: str
    source_crs: str
    maximum_response_bytes: int = Field(gt=0)
    redirect_policy: Literal["DENY"]
    retention_policy: Literal["PRIVATE_INDEFINITE", "MEMORY_ONLY_LT_24H"]
    secret_names: tuple[str, ...]


class TemporalContract(CatalogModel):
    basis: Literal["SOURCE_DATE", "OBSERVED_AT", "RETRIEVED_AT"]
    freshness_days: int = Field(gt=0)
    future_tolerance_days: int = Field(default=0, ge=0)
    refresh_profile: Literal["WEEKLY", "MONTHLY", "YEARLY", "QUARTERLY", "MANUAL", "ON_DEMAND"]


class QualityContract(CatalogModel):
    minimum_rows: int = Field(ge=0)
    maximum_rows: int = Field(gt=0)
    maximum_row_change_ratio: float = Field(ge=0, le=1)
    minimum_coordinate_ratio: float = Field(ge=0, le=1)
    maximum_rejected_ratio: float = Field(ge=0, le=1)


class LicenseContract(CatalogModel):
    status: Literal["APPROVED", "PENDING", "REJECTED"]
    terms_url: str
    terms_fingerprint: str
    reviewed_on: date
    reviewed_by: str
    attribution_text: str
    raw_private_storage_allowed: bool
    internal_derivative_allowed: bool
    public_redistribution_allowed: bool


class TravelSourceContract(CatalogModel):
    id: str
    provider: str
    source_name: str
    landing_url: str
    evidence_grade: Literal["OFFICIAL", "OFFICIAL_AGGREGATOR", "COMMERCIAL_ROUTING", "CURATED"]
    owner: str
    normalization_schema_version: str
    acquisition: AcquisitionContract
    temporal: TemporalContract
    quality: QualityContract
    license: LicenseContract

    @property
    def contract_fingerprint(self) -> str:
        contract = self.model_dump(mode="json")
        if self.temporal.future_tolerance_days == 0:
            contract["temporal"].pop("future_tolerance_days")
        raw = json.dumps(contract, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def is_fresh(
        self,
        *,
        source_date: date | datetime | None = None,
        observed_at: datetime | None = None,
        retrieved_at: datetime | None = None,
        now: datetime | None = None,
    ) -> bool:
        """승인된 temporal basis와 미래 허용 오차로 source snapshot 신선도를 판정한다."""

        checked_at = now or datetime.now(UTC)
        if checked_at.tzinfo is None:
            raise ValueError("SOURCE_FRESHNESS_NOW_TIMEZONE_REQUIRED")
        basis_value: date | datetime | None = {
            "SOURCE_DATE": source_date,
            "OBSERVED_AT": observed_at,
            "RETRIEVED_AT": retrieved_at,
        }[self.temporal.basis]
        if basis_value is None:
            return False
        if isinstance(basis_value, datetime):
            if basis_value.tzinfo is None:
                return False
            age = checked_at.astimezone(UTC) - basis_value.astimezone(UTC)
            return -timedelta(minutes=5) <= age <= timedelta(
                days=self.temporal.freshness_days
            )
        age_days = (checked_at.astimezone(KST).date() - basis_value).days
        return (
            -self.temporal.future_tolerance_days
            <= age_days
            <= self.temporal.freshness_days
        )

    def assert_network_request_allowed(self, url: str) -> None:
        if self.license.status != "APPROVED":
            raise SourceNotApprovedError(f"SOURCE_NOT_APPROVED:{self.id}")
        if self.acquisition.mode not in {"API", "ON_DEMAND"}:
            raise SourceUrlRejectedError(f"SOURCE_NOT_NETWORKED:{self.id}")
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise SourceUrlRejectedError(f"SOURCE_URL_SCHEME_REJECTED:{self.id}")
        if parsed.username is not None or parsed.password is not None:
            raise SourceUrlRejectedError(f"SOURCE_URL_AUTHORITY_REJECTED:{self.id}")
        try:
            port = parsed.port
        except ValueError as error:
            raise SourceUrlRejectedError(f"SOURCE_PORT_REJECTED:{self.id}") from error
        if port not in {None, 443}:
            raise SourceUrlRejectedError(f"SOURCE_PORT_REJECTED:{self.id}")
        if parsed.hostname not in self.acquisition.allowed_hosts:
            raise SourceUrlRejectedError(f"SOURCE_HOST_REJECTED:{self.id}")
        decoded_path = parsed.path
        for _ in range(4):
            next_path = unquote(decoded_path)
            if next_path == decoded_path:
                break
            decoded_path = next_path
        else:
            raise SourceUrlRejectedError(f"SOURCE_PATH_ENCODING_REJECTED:{self.id}")
        if "\\" in decoded_path or "\x00" in decoded_path:
            raise SourceUrlRejectedError(f"SOURCE_PATH_REJECTED:{self.id}")
        if any(segment in {".", ".."} for segment in decoded_path.split("/")):
            raise SourceUrlRejectedError(f"SOURCE_PATH_TRAVERSAL_REJECTED:{self.id}")
        normalized_path = posixpath.normpath(decoded_path)
        if not any(
            normalized_path == prefix.rstrip("/")
            or normalized_path.startswith(f"{prefix.rstrip('/')}/")
            for prefix in self.acquisition.allowed_path_prefixes
        ):
            raise SourceUrlRejectedError(f"SOURCE_PATH_REJECTED:{self.id}")

    def permitted_secrets(self, environment: dict[str, str]) -> dict[str, str]:
        return {
            name: environment[name] for name in self.acquisition.secret_names if name in environment
        }


class SourceCatalog(CatalogModel):
    catalog_version: str
    sources: tuple[TravelSourceContract, ...]

    @classmethod
    def load(cls, path: Path) -> SourceCatalog:
        with path.open("rb") as stream:
            return cls.model_validate(tomllib.load(stream))

    def require(self, source_id: str) -> TravelSourceContract:
        try:
            source = next(item for item in self.sources if item.id == source_id)
        except StopIteration as error:
            raise SourceNotApprovedError(f"SOURCE_UNKNOWN:{source_id}") from error
        if source.license.status != "APPROVED":
            raise SourceNotApprovedError(f"SOURCE_NOT_APPROVED:{source_id}")
        return source


@lru_cache(maxsize=1)
def load_default_source_catalog() -> SourceCatalog:
    """프로세스에서 승인 source 계약을 한 번만 읽어 공통 freshness 원본으로 사용한다."""

    return SourceCatalog.load(DEFAULT_SOURCE_CATALOG_PATH)
