"""명시한 migrator DSN에 append-only SQL migration을 적용한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from jeju_trip.infrastructure.migrations import run_migrations

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    arguments = parser.parse_args()
    run_migrations(arguments.dsn, ROOT / "db" / "migrations")


if __name__ == "__main__":
    main()
