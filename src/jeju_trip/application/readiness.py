"""데이터가 없는 기능을 추정 실행하지 않는 capability readiness gate."""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from jeju_trip.domain.models import (
    EvaluateJejuDayTripInput,
    PreviewTransferInput,
    RecommendDayTripsInput,
)
from jeju_trip.domain.readiness import CapabilityReason, CapabilityState


@dataclass(frozen=True)
class CapabilityGateResult:
    ready: bool
    missing_capabilities: tuple[str, ...]


class ReadinessProvider(Protocol):
    def for_generation(self, request: RecommendDayTripsInput) -> CapabilityGateResult: ...

    def for_evaluation(self, request: EvaluateJejuDayTripInput) -> CapabilityGateResult: ...

    def for_transfer(self, request: PreviewTransferInput) -> CapabilityGateResult: ...

    def for_realtime(
        self, *, uses_bus: bool, trip_date: date | None = None
    ) -> CapabilityGateResult: ...


class CapabilityReadiness:
    def __init__(self, flags: Mapping[str, bool]) -> None:
        self._flags = dict(flags)
        legacy_fare = self._flags.get("fare_policy_ready")
        if legacy_fare is not None:
            self._flags.setdefault("bus_fare_policy_ready", legacy_fare)
            self._flags.setdefault("taxi_fare_policy_ready", legacy_fare)

    def _check(self, required: set[str]) -> CapabilityGateResult:
        missing = tuple(sorted(item for item in required if not self._flags.get(item, False)))
        return CapabilityGateResult(not missing, missing)

    @staticmethod
    def _mode_requirements() -> dict[str, set[str]]:
        return {
            "walk": {"walking_routing_ready"},
            "bus": {
                "walking_routing_ready",
                "future_bus_planning_ready",
                "confirmed_stop_mapping_ready",
                "bus_fare_policy_ready",
            },
            "taxi": {"driving_routing_ready", "taxi_fare_policy_ready"},
        }

    def _for_allowed_modes(self, allowed_modes: Set[str]) -> CapabilityGateResult:
        required = {"service_area_ready", "place_search_ready"}
        mode_requirements = self._mode_requirements()
        for mode in allowed_modes:
            required.update(mode_requirements[mode])
        return self._check(required)

    def for_generation(self, request: RecommendDayTripsInput) -> CapabilityGateResult:
        result = self._for_allowed_modes(request.transport.allowed_modes)
        required = set(result.missing_capabilities)
        if request.party.mobility_support_required or request.walking.avoid_stairs_required:
            required.update({"accessibility_ready", "verified_entrances_ready"})
        if request.food.auto_schedule_meals and (
            request.food.allergens or request.food.excluded_foods
        ):
            required.add("restaurant_recommendation_ready")
        return self._check(required)

    def for_evaluation(self, request: EvaluateJejuDayTripInput) -> CapabilityGateResult:
        return self._for_allowed_modes(request.transport.allowed_modes)

    def for_transfer(self, request: PreviewTransferInput) -> CapabilityGateResult:
        return self._for_allowed_modes(request.allowed_modes)

    def for_realtime(
        self, *, uses_bus: bool, trip_date: date | None = None
    ) -> CapabilityGateResult:
        required = {"realtime_bus_ready"} if uses_bus else set()
        return self._check(required)


class CapabilityFlagRepository(Protocol):
    def capability_states(
        self, trip_date, region_code: str = "JEJU_ALL", grid_id: str = "ALL"
    ) -> dict[str, CapabilityState]: ...

    def capability_flags(
        self, trip_date, region_code: str = "JEJU_ALL", grid_id: str = "ALL"
    ) -> dict[str, bool]: ...

    def exact_bus_planning_available(self, request: RecommendDayTripsInput) -> bool: ...

    def exact_bus_evaluation_available(self, request: EvaluateJejuDayTripInput) -> bool: ...


class PostgresCapabilityReadiness:
    """active coverage와 현재 프로세스에 실제 연결된 adapter를 함께 검사한다."""

    def __init__(
        self,
        repository: CapabilityFlagRepository,
        runtime_flags: Mapping[str, bool],
        *,
        region_code: str = "JEJU_ALL",
        grid_id: str = "ALL",
    ) -> None:
        self._repository = repository
        self._runtime_flags = dict(runtime_flags)
        self._region_code = region_code
        self._grid_id = grid_id

    def _published_for_date(
        self, trip_date: date
    ) -> tuple[dict[str, bool], dict[str, CapabilityState]]:
        state_reader = getattr(self._repository, "capability_states", None)
        if state_reader is not None:
            try:
                states = state_reader(trip_date, self._region_code, self._grid_id)
            except TypeError:
                states = state_reader(trip_date)
            published = {name: state.ready for name, state in states.items()}
        else:
            try:
                published = self._repository.capability_flags(
                    trip_date, self._region_code, self._grid_id
                )
            except TypeError:  # 이전 repository 구현은 원인 없는 false를 missing으로 닫는다.
                published = self._repository.capability_flags(trip_date)
            states = {
                name: (
                    CapabilityState.available()
                    if ready
                    else CapabilityState.unavailable(CapabilityReason.MISSING)
                )
                for name, ready in published.items()
            }
        return published, states

    def _for_date(self, request: RecommendDayTripsInput) -> CapabilityReadiness:
        published, states = self._published_for_date(request.trip_date)
        flags = dict(published)
        future_bus = states.get(
            "future_bus_planning_ready",
            CapabilityState.unavailable(CapabilityReason.MISSING),
        )
        if (
            "bus" in request.transport.allowed_modes
            and future_bus.reason == CapabilityReason.COVERAGE_INCOMPLETE
        ):
            request_probe = getattr(
                self._repository, "exact_bus_planning_available", None
            )
            if request_probe is not None:
                flags["future_bus_planning_ready"] = bool(request_probe(request))
        for capability in ("walking_routing_ready", "driving_routing_ready"):
            flags[capability] = self._runtime_flags.get(capability, False)
        return CapabilityReadiness(flags)

    def _without_exact_probe(self, trip_date: date) -> CapabilityReadiness:
        published, _ = self._published_for_date(trip_date)
        flags = dict(published)
        for capability in ("walking_routing_ready", "driving_routing_ready"):
            flags[capability] = self._runtime_flags.get(capability, False)
        return CapabilityReadiness(flags)

    def _for_evaluation_request(
        self, request: EvaluateJejuDayTripInput
    ) -> CapabilityReadiness:
        published, states = self._published_for_date(request.trip_date)
        flags = dict(published)
        future_bus = states.get(
            "future_bus_planning_ready",
            CapabilityState.unavailable(CapabilityReason.MISSING),
        )
        if (
            "bus" in request.transport.allowed_modes
            and future_bus.reason == CapabilityReason.COVERAGE_INCOMPLETE
        ):
            request_probe = getattr(
                self._repository, "exact_bus_evaluation_available", None
            )
            if request_probe is not None:
                flags["future_bus_planning_ready"] = bool(request_probe(request))
        for capability in ("walking_routing_ready", "driving_routing_ready"):
            flags[capability] = self._runtime_flags.get(capability, False)
        return CapabilityReadiness(flags)

    def for_generation(self, request: RecommendDayTripsInput) -> CapabilityGateResult:
        return self._for_date(request).for_generation(request)

    def for_evaluation(self, request: EvaluateJejuDayTripInput) -> CapabilityGateResult:
        return self._for_evaluation_request(request).for_evaluation(request)

    def for_transfer(self, request: PreviewTransferInput) -> CapabilityGateResult:
        return self._without_exact_probe(request.departure_at.date()).for_transfer(request)

    def for_realtime(
        self, *, uses_bus: bool, trip_date: date | None = None
    ) -> CapabilityGateResult:
        if trip_date is None:
            return CapabilityReadiness(self._runtime_flags).for_realtime(uses_bus=uses_bus)
        try:
            published = self._repository.capability_flags(
                trip_date, self._region_code, self._grid_id
            )
        except TypeError:
            published = self._repository.capability_flags(trip_date)
        ready = published.get("confirmed_stop_mapping_ready", False) and self._runtime_flags.get(
            "realtime_bus_ready", False
        )
        return CapabilityReadiness({"realtime_bus_ready": ready}).for_realtime(uses_bus=uses_bus)
