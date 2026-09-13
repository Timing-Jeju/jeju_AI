"""원본·geometry를 수용하지 않는 저장용 일정 projection 계약."""

from __future__ import annotations

from datetime import datetime
from itertools import combinations
from typing import Annotated, Literal

from pydantic import Field, model_validator

from jeju_trip.domain.models import (
    ContractModel,
    RecommendationSegmentRisk,
    Strategy,
    Totals,
    _require_kst,
    route_signatures_are_materially_different,
)


class FactProvenance(ContractModel):
    fact_id: str
    source_ids: tuple[str, ...]
    input_fact_ids: tuple[str, ...]


class DurableEvent(ContractModel):
    event_id: str
    sequence: Annotated[int, Field(gt=0)]
    type: Literal["visit", "transfer", "rest", "meal", "buffer"]
    start_at: datetime
    end_at: datetime
    duration_minutes: Annotated[int, Field(ge=0)]
    place_id: str | None
    mode: Literal["walk", "bus", "taxi"] | None
    distance_meters: Annotated[int | None, Field(ge=0)]
    evidence_fact_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_event(self) -> DurableEvent:
        _require_kst(self.start_at, "start_at")
        _require_kst(self.end_at, "end_at")
        if (self.end_at - self.start_at).total_seconds() != self.duration_minutes * 60:
            raise ValueError("event duration mismatch")
        if (self.type == "transfer") != (self.mode is not None):
            raise ValueError("only transfers require a mode")
        return self


class DurableCandidate(ContractModel):
    route_id: str
    place_ids: tuple[str, ...] = Field(min_length=1)
    rank: Annotated[int, Field(ge=1, le=3)]
    strategy: Strategy
    feasibility: Literal["feasible", "feasible_with_caution"]
    score: Annotated[float, Field(ge=0, le=100)]
    start_place_id: str
    end_place_id: str
    events: tuple[DurableEvent, ...] = Field(min_length=1)
    totals: Totals
    segment_risks: tuple[RecommendationSegmentRisk, ...]

    @model_validator(mode="after")
    def validate_timeline(self) -> DurableCandidate:
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("duplicate event identity")
        for previous, current in zip(self.events, self.events[1:], strict=False):
            if previous.sequence >= current.sequence or previous.end_at > current.start_at:
                raise ValueError("events must be ordered and non-overlapping")
        if sum(event.duration_minutes for event in self.events) != self.totals.total_minutes:
            raise ValueError("event total mismatch")
        for kind in ("visit", "transfer", "rest", "meal", "buffer"):
            if sum(e.duration_minutes for e in self.events if e.type == kind) != getattr(
                self.totals, f"{kind}_minutes"
            ):
                raise ValueError("event type total mismatch")
        event_ids = {event.event_id for event in self.events}
        if any(risk.event_id not in event_ids for risk in self.segment_risks):
            raise ValueError("unknown risk event")
        return self


class DurableCandidateSet(ContractModel):
    projection_version: Literal["schedule-projection.v1"] = "schedule-projection.v1"
    request_id: str
    generated_at: datetime
    expires_at: datetime
    candidates: tuple[DurableCandidate, ...] = Field(min_length=3, max_length=3)
    provenance: tuple[FactProvenance, ...]

    @model_validator(mode="after")
    def validate_closed_result(self) -> DurableCandidateSet:
        for value in (self.generated_at, self.expires_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timezone required")
        if self.expires_at <= self.generated_at:
            raise ValueError("expiry must follow generation")
        if {candidate.strategy for candidate in self.candidates} != set(Strategy):
            raise ValueError("exactly three strategies required")
        signatures = [
            (
                candidate.place_ids,
                tuple(e.mode for e in candidate.events if e.mode and e.mode != "walk"),
                tuple(e.duration_minutes for e in candidate.events
                      if e.type in {"visit", "meal", "rest"}),
            )
            for candidate in self.candidates
        ]
        if not all(route_signatures_are_materially_different(a, b)
                   for a, b in combinations(signatures, 2)):
            raise ValueError("materially different routes required")
        if len({candidate.route_id for candidate in self.candidates}) != 3:
            raise ValueError("duplicate route identity")
        known = {fact.fact_id for fact in self.provenance}
        if len(known) != len(self.provenance):
            raise ValueError("duplicate fact identity")
        references = {ref for fact in self.provenance for ref in fact.input_fact_ids}
        for candidate in self.candidates:
            references.update(candidate.totals.derivation_evidence_fact_ids)
            for event in candidate.events:
                references.update(event.evidence_fact_ids)
            for risk in candidate.segment_risks:
                references.update(risk.evidence_fact_ids)
        if not references.issubset(known):
            raise ValueError("unknown evidence identity")
        remaining = {fact.fact_id: set(fact.input_fact_ids) for fact in self.provenance}
        while remaining:
            ready = {key for key, inputs in remaining.items() if not inputs}
            if not ready:
                raise ValueError("cyclic evidence lineage")
            remaining = {
                key: inputs - ready for key, inputs in remaining.items() if key not in ready
            }
        return self
