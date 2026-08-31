"""v0.6 전역·다일 공개 계약 예시를 synthetic 근거로 생성한다."""

from __future__ import annotations

import argparse
from pathlib import Path

import generate_v05_synthetic_examples as synthetic

ROOT = Path(__file__).resolve().parents[1]


def main(output: Path | None = None) -> None:
    """보관된 v0.5 예시를 건드리지 않고 현재 계약 전용 경로에 생성한다."""

    synthetic.OUTPUT = output or ROOT / "docs/examples/v0.6/synthetic"
    synthetic.EXAMPLE_SCHEMA_VERSION = "0.6.0"
    synthetic.GENERATOR_NAME = "scripts/generate_v06_synthetic_examples.py"
    synthetic.main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    main(arguments.output)
