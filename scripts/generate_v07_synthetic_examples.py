"""v0.7 FE 경계·체류시간 공개 계약 예시를 synthetic 근거로 생성한다."""

from __future__ import annotations

import argparse
from pathlib import Path

import generate_v05_synthetic_examples as synthetic

ROOT = Path(__file__).resolve().parents[1]


def main(output: Path | None = None) -> None:
    """보관된 v0.5·v0.6 예시를 건드리지 않고 v0.7 경로에 생성한다."""

    synthetic.OUTPUT = output or ROOT / "docs/examples/v0.7/synthetic"
    synthetic.EXAMPLE_SCHEMA_VERSION = "0.7.0"
    synthetic.GENERATOR_NAME = "scripts/generate_v07_synthetic_examples.py"
    synthetic.main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    main(arguments.output)
