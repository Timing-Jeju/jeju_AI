"""Pydantic 공개 모델에서 커밋할 JSON Schema를 생성한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from jeju_trip.domain.durable_projection import DurableCandidateSet
from jeju_trip.domain.models import (
    BusStopInspection,
    DayTripResponse,
    EvaluateJejuDayTripInput,
    EvaluationResponse,
    InspectBusStopInput,
    PreviewTransferInput,
    PreviewTransferResponse,
    RecommendDayTripsInput,
    RevalidateJejuDayTripInput,
    RevalidationResponse,
    SearchPlacesInput,
    SearchPlacesResponse,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = ROOT / "docs" / "contracts"
MODELS = {
    "durable-candidate-set.schema.json": DurableCandidateSet,
    "recommend-day-trips.input.schema.json": RecommendDayTripsInput,
    "day-trip-recommendations.schema.json": DayTripResponse,
    "evaluate-day-trip.input.schema.json": TypeAdapter(EvaluateJejuDayTripInput),
    "evaluate-day-trip.output.schema.json": EvaluationResponse,
    "revalidate-day-trip.input.schema.json": RevalidateJejuDayTripInput,
    "revalidate-day-trip.output.schema.json": RevalidationResponse,
    "search-jeju-places.input.schema.json": SearchPlacesInput,
    "search-jeju-places.output.schema.json": SearchPlacesResponse,
    "inspect-jeju-bus-stop.input.schema.json": InspectBusStopInput,
    "inspect-jeju-bus-stop.output.schema.json": BusStopInspection,
    "preview-jeju-transfer.input.schema.json": PreviewTransferInput,
    "preview-jeju-transfer.output.schema.json": PreviewTransferResponse,
}


def main(output: Path | None = None) -> None:
    directory = output or SCHEMA_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    for filename, model in MODELS.items():
        target = directory / filename
        schema = (
            model.json_schema() if isinstance(model, TypeAdapter) else model.model_json_schema()
        )
        rendered = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        target.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    main(arguments.output)
