"""장소 확정과 공간 scope 판정을 capability gate보다 먼저 수행한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from jeju_trip.domain.models import RecommendDayTripsInput


@dataclass(frozen=True)
class PreparedRequest:
    request: RecommendDayTripsInput
    region_code: str
    grid_id: str


class RequestPreparationFailure(ValueError):
    """추정 없이 요청 준비를 중단해야 하는 구조화 실패."""

    def __init__(self, code: str, reason_codes: tuple[str, ...]) -> None:
        super().__init__(code)
        self.code = code
        self.reason_codes = reason_codes


class PreparationRepository(Protocol):
    def resolve_request(self, request: RecommendDayTripsInput) -> RecommendDayTripsInput: ...

    def places_covered_by_jeju(self, place_ids: tuple[str, ...]) -> bool: ...

    def places_in_scope(
        self, place_ids: tuple[str, ...], region_code: str, grid_id: str
    ) -> bool: ...


class RequestPreparation:
    """장소 ID를 확정한 뒤 제주 전역 active 경계만 강제한다."""

    def __init__(
        self,
        repository: PreparationRepository,
        *,
        region_code: str = "JEJU_ALL",
        grid_id: str = "ALL",
    ) -> None:
        self._repository = repository
        self._region_code = region_code
        self._grid_id = grid_id

    def prepare(self, request: RecommendDayTripsInput) -> PreparedRequest:
        resolved = self._repository.resolve_request(request)
        place_ids = self._place_ids(resolved)
        if not place_ids:
            raise RequestPreparationFailure("PLACE_AMBIGUOUS", ("PLACE_UNRESOLVED",))
        if not self._repository.places_covered_by_jeju(place_ids):
            raise RequestPreparationFailure(
                "REGION_SCOPE_NOT_READY", ("OUTSIDE_JEJU_SERVICE_AREA",)
            )
        return PreparedRequest(resolved, self._region_code, self._grid_id)

    @staticmethod
    def _place_ids(request: RecommendDayTripsInput) -> tuple[str, ...]:
        values = (
            request.accommodation.place_id,
            *(item.place_id for item in request.required_places),
            *(item.place_id for item in request.preferred_places),
        )
        if any(value is None or not value.strip() for value in values):
            raise RequestPreparationFailure("PLACE_AMBIGUOUS", ("PLACE_UNRESOLVED",))
        return tuple(value for value in values if value is not None)
