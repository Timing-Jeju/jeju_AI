"""projection을 삭제하지 않고 이전 publication으로 active pointer를 되돌린다."""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg import sql


class PostgresActivationRepository:
    def __init__(self, importer_dsn: str, assume_role: str | None = None) -> None:
        self._importer_dsn = importer_dsn
        self._assume_role = assume_role

    def rollback(self, source_id: str, publication_id: UUID) -> None:
        """같은 source의 기존 publication만 원자적으로 다시 활성화한다."""

        with psycopg.connect(self._importer_dsn) as connection:
            if self._assume_role:
                connection.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._assume_role))
                )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (source_id,)
            )
            target = connection.execute(
                """SELECT publication_id FROM source_admin.publication
                   WHERE source_id = %s AND publication_id = %s""",
                (source_id, publication_id),
            ).fetchone()
            if target is None:
                raise ValueError("ROLLBACK_PUBLICATION_NOT_FOUND_FOR_SOURCE")
            previous = connection.execute(
                """SELECT publication_id FROM source_admin.active_snapshot
                   WHERE source_id = %s FOR UPDATE""",
                (source_id,),
            ).fetchone()
            if previous is None:
                raise ValueError("ACTIVE_PUBLICATION_MISSING")
            activated = connection.execute(
                """UPDATE source_admin.active_snapshot
                   SET publication_id = %s, activated_at = clock_timestamp()
                   WHERE source_id = %s RETURNING activated_at""",
                (publication_id, source_id),
            ).fetchone()
            if activated is None:
                raise RuntimeError("ROLLBACK_ACTIVATION_FAILED")
            connection.execute(
                """INSERT INTO source_admin.activation_event(
                       source_id, previous_publication_id, publication_id, activated_at
                   ) VALUES (%s, %s, %s, %s)""",
                (source_id, previous[0], publication_id, activated[0]),
            )
