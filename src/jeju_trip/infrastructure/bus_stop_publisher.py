"""검증된 TAGO 정류장을 append-only publication으로 원자적으로 발행한다."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from jeju_trip.infrastructure.public_data_normalizers import TagoBusStopRecord


@dataclass(frozen=True)
class BusStopPublication:
    publication_id: UUID
    source_id: str
    dataset_version: str
    published_at: datetime
    record_count: int
    created: bool


class PostgresBusStopPublisher:
    """VALIDATED 정류장 projection을 active pointer 변경 없이 STAGED로 발행한다."""

    def __init__(self, importer_dsn: str, assume_role: str | None = None) -> None:
        self._importer_dsn = importer_dsn
        self._assume_role = assume_role

    def _set_role(self, connection: psycopg.Connection[tuple[object, ...]]) -> None:
        if self._assume_role:
            connection.execute(
                sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._assume_role))
            )

    def publish(
        self,
        acquisition_id: UUID,
        records: Iterable[TagoBusStopRecord],
    ) -> BusStopPublication:
        materialized = tuple(records)
        if not materialized:
            raise ValueError("PUBLICATION_RECORDS_EMPTY")

        with psycopg.connect(self._importer_dsn) as connection:
            self._set_role(connection)
            acquisition = connection.execute(
                """SELECT source_id, status, raw_checksum, normalized_checksum,
                          normalization_schema_version, temporal_basis, source_date,
                          observed_at, collected_at
                   FROM source_admin.acquisition
                   WHERE acquisition_id = %s
                   FOR UPDATE""",
                (acquisition_id,),
            ).fetchone()
            if acquisition is None:
                raise ValueError("ACQUISITION_NOT_FOUND")
            (
                source_id,
                status,
                raw_checksum,
                normalized_checksum,
                normalization_schema_version,
                temporal_basis,
                source_date,
                observed_at,
                collected_at,
            ) = acquisition
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source_id,)
            )
            if status in {"STAGED", "PUBLISHED"}:
                existing = connection.execute(
                    """SELECT publication_id, dataset_version, published_at
                       FROM source_admin.publication WHERE acquisition_id = %s""",
                    (acquisition_id,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("PUBLISHED_ACQUISITION_WITHOUT_PUBLICATION")
                return BusStopPublication(
                    publication_id=existing[0],
                    source_id=str(source_id),
                    dataset_version=str(existing[1]),
                    published_at=existing[2],
                    record_count=len(materialized),
                    created=False,
                )
            if status != "VALIDATED":
                raise ValueError("ACQUISITION_NOT_VALIDATED")
            if not normalized_checksum:
                raise ValueError("NORMALIZED_CHECKSUM_MISSING")

            effective_observed = observed_at or collected_at
            data_as_of = self._data_as_of(source_date, effective_observed)
            dataset_version = f"{data_as_of.isoformat()}-{normalized_checksum[:12]}"
            publication = connection.execute(
                """INSERT INTO source_admin.publication(
                       source_id, acquisition_id, dataset_version, raw_checksum,
                       normalized_checksum, normalization_schema_version, temporal_basis,
                       source_date, observed_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING publication_id, published_at""",
                (
                    source_id,
                    acquisition_id,
                    dataset_version,
                    raw_checksum,
                    normalized_checksum,
                    normalization_schema_version,
                    temporal_basis,
                    source_date,
                    observed_at,
                ),
            ).fetchone()
            if publication is None:
                raise RuntimeError("PUBLICATION_INSERT_FAILED")
            publication_id, published_at = publication

            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO travel_projection.bus_stop_fact(
                           publication_id, source_id, fact_id, source_record_id,
                           provider_stop_id, name, direction_text, position,
                           data_as_of, observed_at, attributes
                       ) VALUES (
                           %s, %s, %s, %s, %s, %s, %s,
                           ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                           %s, %s, %s
                       )""",
                    [
                        (
                            publication_id,
                            source_id,
                            record.fact_id,
                            record.provider_stop_id,
                            record.provider_stop_id,
                            record.name,
                            record.direction_text,
                            record.position.longitude,
                            record.position.latitude,
                            data_as_of,
                            effective_observed,
                            Jsonb({"city_code": record.city_code}),
                        )
                        for record in materialized
                    ],
                )

            updated = connection.execute(
                """UPDATE source_admin.acquisition SET status = 'STAGED'
                   WHERE acquisition_id = %s AND status = 'VALIDATED'""",
                (acquisition_id,),
            )
            if updated.rowcount != 1:
                raise RuntimeError("PUBLICATION_STATE_TRANSITION_FAILED")
            return BusStopPublication(
                publication_id=publication_id,
                source_id=str(source_id),
                dataset_version=dataset_version,
                published_at=published_at,
                record_count=len(materialized),
                created=True,
            )

    @staticmethod
    def _data_as_of(source_date: date | None, observed_at: datetime) -> date:
        return source_date or observed_at.date()
