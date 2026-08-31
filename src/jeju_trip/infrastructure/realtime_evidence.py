"""TAGO 도착예측으로 정적 버스 대기시간만 교체하는 일시적 판정 근거."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from jeju_trip.application.service import RealtimeEvidencePreparation
from jeju_trip.domain.models import (
    DataSourceMetadata,
    Derivation,
    EvidenceFact,
    FullTimelineEvaluationInput,
    FullTimelineTransfer,
    RevalidateJejuDayTripInput,
    SourceRef,
)
from jeju_trip.infrastructure.realtime_bus_adapter import (
    RealtimeArrivalSnapshot,
    RealtimeBusArrivalAdapter,
)
from jeju_trip.infrastructure.source_catalog import TravelSourceContract
from jeju_trip.planning.evaluation import EvaluationEvidence
from jeju_trip.planning.execution_budget import ExecutionBudget


class RealtimeStopMappingResolver(Protocol):
    def resolve_stop(self, canonical_stop_id: str) -> tuple[str, str, str] | None:
        """city code, provider stop ID, mapping status를 반환한다."""


class TagoRealtimeEvaluationEvidence:
    """최대 두 정류장의 최신 snapshot을 메모리에만 보유해 route fact를 갱신한다."""

    def __init__(
        self,
        base: EvaluationEvidence,
        adapter: RealtimeBusArrivalAdapter,
        contract: TravelSourceContract,
        resolver: RealtimeStopMappingResolver,
        environment: dict[str, str],
        request: RevalidateJejuDayTripInput,
        budget: ExecutionBudget,
    ) -> None:
        self._base = base
        self._adapter = adapter
        self._contract = contract
        self._resolver = resolver
        self._environment = environment
        self._request = request
        self._budget = budget
        self._snapshots: dict[str, RealtimeArrivalSnapshot] = {}
        self._arrival_facts: dict[str, EvidenceFact] = {}

    def route(self, from_place_id: str, to_place_id: str, departure_at):
        route = self._base.route(from_place_id, to_place_id, departure_at)
        return self._apply_realtime(route, from_place_id, to_place_id, departure_at)

    def route_for_mode(self, from_place_id, to_place_id, departure_at, mode):
        reader = getattr(self._base, "route_for_mode", None)
        route = (
            reader(from_place_id, to_place_id, departure_at, mode)
            if reader is not None
            else self._base.route(from_place_id, to_place_id, departure_at)
        )
        return self._apply_realtime(route, from_place_id, to_place_id, departure_at)

    def _apply_realtime(self, route, from_place_id, to_place_id, departure_at):
        if self._request.progress.state != "waiting_bus":
            return route
        current_decision_matches = bool(
            route is not None
            and route.mode == "bus"
            and route.boarding_stop_id == self._request.progress.current_stop_id
            and route.provider_route_id == self._request.progress.current_route_id
        )
        if not current_decision_matches:
            planned = self._verified_planned_current_route(
                from_place_id, to_place_id, departure_at
            )
            if planned is None:
                return route
            route = planned
        if route is None or route.mode != "bus":
            return route
        if not route.boarding_stop_id or not route.provider_route_id:
            return None
        mapping = self._resolver.resolve_stop(route.boarding_stop_id)
        if mapping is None or mapping[2] != "CONFIRMED":
            return None
        city_code, provider_stop_id, mapping_status = mapping
        snapshot = self._snapshots.get(provider_stop_id)
        if snapshot is None:
            if len(self._snapshots) >= 1:
                return route
            self._budget.claim_external_call("tago.bus-arrival")
            try:
                snapshot = self._adapter.fetch(
                    self._contract,
                    city_code=city_code,
                    node_id=provider_stop_id,
                    environment=self._environment,
                )
            except Exception:
                return None
            self._snapshots[provider_stop_id] = snapshot
        if snapshot.is_stale(self._request.checked_at):
            return None
        try:
            arrival = snapshot.for_planned_route(route.provider_route_id, mapping_status)
        except ValueError:
            return None
        realtime_wait = math.ceil(arrival.arrival_seconds / 60)
        self._arrival_facts[arrival.fact_id] = EvidenceFact(
            fact_id=arrival.fact_id,
            category="realtime_bus_arrival",
            value={
                "arrival_seconds": arrival.arrival_seconds,
                "remaining_stops": arrival.remaining_stops,
                "expected_arrival_at": arrival.expected_arrival_at.isoformat(),
            },
            source_refs=(
                SourceRef(
                    source_id=self._contract.id,
                    source_fact_id=arrival.fact_id,
                ),
            ),
            data_as_of=arrival.checked_at,
            retrieved_at=arrival.checked_at,
            confidence=1,
            derivation=Derivation(kind="source"),
        )
        duration = max(0, route.duration_minutes - route.scheduled_wait_minutes) + realtime_wait
        return replace(
            route,
            duration_minutes=duration,
            evidence_fact_ids=(*route.evidence_fact_ids, arrival.fact_id),
        )

    def _verified_planned_current_route(
        self, from_place_id: str, to_place_id: str, departure_at
    ):
        """현재 최적편이 바뀌어도 계획시각의 선택편을 독립 재조회해 exact claim을 확인한다."""

        if departure_at != self._request.checked_at:
            return None
        itinerary = self._request.itinerary
        if not isinstance(itinerary, FullTimelineEvaluationInput):
            return None
        event = next(
            (
                item
                for item in itinerary.timeline
                if isinstance(item, FullTimelineTransfer)
                and item.event_id == self._request.progress.current_event_id
                and item.planned_mode == "bus"
            ),
            None,
        )
        if event is None or (
            event.planned_route_id != self._request.progress.current_route_id
            or event.planned_boarding_stop_id != self._request.progress.current_stop_id
        ):
            return None
        reader = getattr(self._base, "route_for_mode", None)
        if reader is None:
            return None
        self._budget.claim_external_call("routing.selected-bus-verification")
        planned = reader(from_place_id, to_place_id, event.start_at, "bus")
        if planned is None or planned.mode != "bus":
            return None
        claim_pairs = (
            (event.planned_route_id, planned.provider_route_id),
            (event.planned_route_number, planned.route_number),
            (event.planned_boarding_stop_id, planned.boarding_stop_id),
            (event.planned_alighting_stop_id, planned.alighting_stop_id),
            (event.scheduled_departure_at, planned.scheduled_departure_at),
            (event.scheduled_arrival_at, planned.scheduled_arrival_at),
        )
        return planned if all(expected == actual for expected, actual in claim_pairs) else None

    def opening_window(self, place_id, on_date):
        return self._base.opening_window(place_id, on_date)

    def opening_windows(self, place_id, on_date):
        reader = getattr(self._base, "opening_windows", None)
        if reader is not None:
            return reader(place_id, on_date)
        opening = self._base.opening_window(place_id, on_date)
        return (opening,) if opening is not None else ()

    def evidence_facts(self):
        reader = getattr(self._base, "evidence_facts", None)
        base = reader() if reader is not None else ()
        return (*base, *self._arrival_facts.values())

    def data_sources(self):
        reader = getattr(self._base, "data_sources", None)
        base = reader() if reader is not None else ()
        if not self._arrival_facts:
            return base
        realtime = DataSourceMetadata(
            source_id=self._contract.id,
            provider=self._contract.provider,
            dataset_version=None,
            data_as_of=max(fact.data_as_of for fact in self._arrival_facts.values()),
            retrieved_at=datetime.now(UTC),
            status="ACTIVE",
            attribution_text=self._contract.license.attribution_text,
        )
        return (*base, realtime)

    def dietary_safety(self, place_id, allergens, excluded_foods, on_date):
        checker = getattr(self._base, "dietary_safety", None)
        return checker(place_id, allergens, excluded_foods, on_date) if checker else None


class TagoRealtimeEvidenceProvider:
    def __init__(
        self,
        base: EvaluationEvidence | None,
        adapter: RealtimeBusArrivalAdapter,
        contract: TravelSourceContract,
        resolver: RealtimeStopMappingResolver,
        environment: dict[str, str],
    ) -> None:
        self._base = base
        self._adapter = adapter
        self._contract = contract
        self._resolver = resolver
        self._environment = environment

    def prepare(
        self, request: RevalidateJejuDayTripInput, budget: ExecutionBudget
    ) -> RealtimeEvidencePreparation:
        if self._base is None:
            return RealtimeEvidencePreparation(None, ("REALTIME_BASE_EVIDENCE_UNAVAILABLE",))
        return RealtimeEvidencePreparation(
            TagoRealtimeEvaluationEvidence(
                self._base,
                self._adapter,
                self._contract,
                self._resolver,
                self._environment,
                request,
                budget,
            ),
            ("REALTIME_CURRENT_BOARDING_DECISION_ONLY",),
        )

    def prepare_with_base(
        self,
        request: RevalidateJejuDayTripInput,
        budget: ExecutionBudget,
        base: EvaluationEvidence,
    ) -> RealtimeEvidencePreparation:
        return RealtimeEvidencePreparation(
            TagoRealtimeEvaluationEvidence(
                base,
                self._adapter,
                self._contract,
                self._resolver,
                self._environment,
                request,
                budget,
            ),
            ("REALTIME_CURRENT_BOARDING_DECISION_ONLY",),
        )
