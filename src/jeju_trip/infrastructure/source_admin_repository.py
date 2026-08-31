"""검증된 raw pointer와 acquisition을 importer transaction으로 등록한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from jeju_trip.infrastructure.raw_store import RawObjectPointer
from jeju_trip.infrastructure.source_catalog import TravelSourceContract


@dataclass(frozen=True)
class AcquisitionRegistration:
    acquisition_id: UUID
    created: bool
    status: str


class PostgresSourceAdminRepository:
    def __init__(self, importer_dsn: str, assume_role: str | None = None) -> None:
        self._importer_dsn = importer_dsn
        self._assume_role = assume_role

    def _set_role(self, connection: psycopg.Connection[tuple[object, ...]]) -> None:
        if self._assume_role:
            connection.execute(
                sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._assume_role))
            )

    def register_verified_raw(
        self,
        contract: TravelSourceContract,
        pointer: RawObjectPointer,
        temporal_basis: Literal["SOURCE_DATE", "OBSERVED_AT", "RETRIEVED_AT"],
        source_date: date | None,
        observed_at: datetime | None,
        raw_row_count: int | None,
        initial_status: Literal["INCOMPLETE", "ACQUIRED"] = "ACQUIRED",
    ) -> AcquisitionRegistration:
        if pointer.source_id != contract.id:
            raise ValueError("RAW_POINTER_SOURCE_MISMATCH")
        with psycopg.connect(self._importer_dsn) as connection:
            self._set_role(connection)
            connection.execute(
                """INSERT INTO source_admin.data_source(
                source_id, provider, source_name, landing_url, evidence_grade, owner_name
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_id) DO NOTHING""",
                (
                    contract.id,
                    contract.provider,
                    contract.source_name,
                    contract.landing_url,
                    contract.evidence_grade,
                    contract.owner,
                ),
            )
            contract_id_row = connection.execute(
                """INSERT INTO source_admin.source_contract(
                source_id, contract_fingerprint, contract, normalization_schema_version,
                license_status, reviewed_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_id, contract_fingerprint) DO NOTHING
                RETURNING contract_id""",
                (
                    contract.id,
                    contract.contract_fingerprint,
                    Jsonb(contract.model_dump(mode="json")),
                    contract.normalization_schema_version,
                    contract.license.status,
                    contract.license.reviewed_on,
                ),
            ).fetchone()
            if contract_id_row is None:
                contract_id_row = connection.execute(
                    """SELECT contract_id FROM source_admin.source_contract
                    WHERE source_id = %s AND contract_fingerprint = %s""",
                    (contract.id, contract.contract_fingerprint),
                ).fetchone()
            if contract_id_row is None:
                raise RuntimeError("SOURCE_CONTRACT_REGISTRATION_FAILED")
            contract_id = contract_id_row[0]

            connection.execute(
                """INSERT INTO source_admin.raw_object(
                source_id, checksum, bucket, object_key, object_version_id, content_type,
                byte_length, retention_policy
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_id, checksum) DO NOTHING""",
                (
                    pointer.source_id,
                    pointer.checksum,
                    pointer.bucket,
                    pointer.object_key,
                    pointer.object_version_id,
                    pointer.content_type,
                    pointer.byte_length,
                    contract.acquisition.retention_policy,
                ),
            )
            acquisition_row = connection.execute(
                """INSERT INTO source_admin.acquisition(
                source_id, contract_id, raw_checksum, temporal_basis, source_date,
                observed_at, status, raw_row_count, normalization_schema_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_id, raw_checksum, normalization_schema_version) DO NOTHING
                RETURNING acquisition_id, status""",
                (
                    contract.id,
                    contract_id,
                    pointer.checksum,
                    temporal_basis,
                    source_date,
                    observed_at,
                    initial_status,
                    raw_row_count,
                    contract.normalization_schema_version,
                ),
            ).fetchone()
            if acquisition_row is not None:
                return AcquisitionRegistration(
                    acquisition_id=acquisition_row[0],
                    created=True,
                    status=str(acquisition_row[1]),
                )
            existing = connection.execute(
                """SELECT acquisition_id, status FROM source_admin.acquisition
                WHERE source_id = %s AND raw_checksum = %s
                  AND normalization_schema_version = %s""",
                (
                    contract.id,
                    pointer.checksum,
                    contract.normalization_schema_version,
                ),
            ).fetchone()
            if existing is None:
                raise RuntimeError("ACQUISITION_REGISTRATION_FAILED")
            return AcquisitionRegistration(
                acquisition_id=existing[0],
                created=False,
                status=str(existing[1]),
            )

    def mark_validated(
        self,
        acquisition_id: UUID,
        accepted_row_count: int,
        rejected_row_count: int,
        normalized_checksum: str,
    ) -> None:
        with psycopg.connect(self._importer_dsn) as connection:
            self._set_role(connection)
            cursor = connection.execute(
                """UPDATE source_admin.acquisition
                SET status = 'VALIDATED', accepted_row_count = %s, rejected_row_count = %s,
                    normalized_checksum = %s, validated_at = clock_timestamp()
                WHERE acquisition_id = %s AND status = 'ACQUIRED'""",
                (
                    accepted_row_count,
                    rejected_row_count,
                    normalized_checksum,
                    acquisition_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("ACQUISITION_STATE_TRANSITION_REJECTED")

    def mark_quality_failed(
        self,
        acquisition_id: UUID,
        accepted_row_count: int,
        rejected_row_count: int,
    ) -> None:
        with psycopg.connect(self._importer_dsn) as connection:
            self._set_role(connection)
            cursor = connection.execute(
                """UPDATE source_admin.acquisition
                SET status = 'QUALITY_FAILED', accepted_row_count = %s, rejected_row_count = %s,
                    validated_at = clock_timestamp()
                WHERE acquisition_id = %s AND status = 'ACQUIRED'""",
                (accepted_row_count, rejected_row_count, acquisition_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("ACQUISITION_STATE_TRANSITION_REJECTED")

    def previous_published_row_count(self, source_id: str) -> int | None:
        """현재 active publication acquisition의 원본 행 수를 품질 기준으로 조회한다."""

        with psycopg.connect(self._importer_dsn) as connection:
            self._set_role(connection)
            row = connection.execute(
                """SELECT acquisition.raw_row_count
                   FROM source_admin.active_snapshot active
                   JOIN source_admin.publication publication
                     ON publication.publication_id = active.publication_id
                   JOIN source_admin.acquisition acquisition
                     ON acquisition.acquisition_id = publication.acquisition_id
                   WHERE active.source_id = %s""",
                (source_id,),
            ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else None
