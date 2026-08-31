"""외부 네트워크 전에 source 승인·보존·secret 준비상태를 판정한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from jeju_trip.infrastructure.source_catalog import TravelSourceContract

SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PreflightResult:
    source_id: str
    status: Literal["PASS", "FAIL"]
    reason_codes: tuple[str, ...]
    permitted_secret_names: tuple[str, ...]
    evidence: tuple[str, ...]


class SourcePreflight:
    """값을 노출하지 않고 source별 secret 이름과 계약 일관성만 확인한다."""

    def check(self, contract: TravelSourceContract, environment: dict[str, str]) -> PreflightResult:
        failures: list[str] = []
        evidence: list[str] = [
            f"CONTRACT_FINGERPRINT:{contract.contract_fingerprint}",
            f"LICENSE_STATUS:{contract.license.status}",
        ]
        if contract.license.status != "APPROVED":
            failures.append("SOURCE_NOT_APPROVED")

        external_source = contract.acquisition.mode in {"API", "ON_DEMAND"}
        if external_source and not SHA256.fullmatch(contract.license.terms_fingerprint):
            failures.append("SOURCE_TERMS_EVIDENCE_INVALID")

        retention = contract.acquisition.retention_policy
        if retention == "PRIVATE_INDEFINITE" and not contract.license.raw_private_storage_allowed:
            failures.append("SOURCE_RETENTION_CONTRACT_INVALID")
        if retention == "MEMORY_ONLY_LT_24H":
            if contract.license.raw_private_storage_allowed:
                failures.append("SOURCE_RETENTION_CONTRACT_INVALID")
            else:
                evidence.append("MEMORY_ONLY_RETENTION")

        permitted = tuple(
            name for name in contract.acquisition.secret_names if environment.get(name)
        )
        if external_source and len(permitted) != len(contract.acquisition.secret_names):
            failures.append("SOURCE_SECRET_MISSING")

        return PreflightResult(
            source_id=contract.id,
            status="FAIL" if failures else "PASS",
            reason_codes=tuple(dict.fromkeys(failures)),
            permitted_secret_names=permitted,
            evidence=tuple(evidence),
        )
