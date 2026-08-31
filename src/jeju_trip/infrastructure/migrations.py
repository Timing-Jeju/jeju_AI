"""적용된 SQL checksum 변경을 거부하는 append-only migration runner."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import LiteralString, cast

import psycopg
from psycopg import sql


class MigrationChecksumMismatch(RuntimeError):
    """이미 적용된 migration 파일이 변경된 경우."""


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_migrations(dsn: str, directory: Path) -> None:
    files = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(dsn, autocommit=True) as connection:
        tracking_table = connection.execute(
            "SELECT to_regclass('source_admin.schema_migration')"
        ).fetchone()
        applied = (
            dict(
                connection.execute(
                    "SELECT version, checksum FROM source_admin.schema_migration"
                ).fetchall()
            )
            if tracking_table and tracking_table[0] is not None
            else {}
        )
        for path in files:
            version = path.name.split("_", 1)[0]
            checksum = _checksum(path)
            if version in applied:
                if applied[version] != checksum:
                    raise MigrationChecksumMismatch(f"MIGRATION_CHECKSUM_MISMATCH:{version}")
                continue
            with connection.transaction():
                migration_sql = cast(LiteralString, path.read_text(encoding="utf-8"))
                connection.execute(sql.SQL(migration_sql))
                connection.execute(
                    "INSERT INTO source_admin.schema_migration(version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )
