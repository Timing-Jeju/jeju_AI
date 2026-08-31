"""capability gate 전에 수행하는 v0.6 전역 요청 준비 순서를 검증한다."""

from __future__ import annotations

import pytest

from jeju_trip.application.preparation import RequestPreparation, RequestPreparationFailure
from tests.factories import make_request


class PreparationFixture:
    """장소 확정·제주 경계·동부 scope 결과를 제어한다."""

    def __init__(self, *, covered: bool = True, in_scope: bool = True) -> None:
        self.covered = covered
        self.in_scope = in_scope

    def resolve_request(self, request):
        payload = request.model_dump(mode="python")
        payload["accommodation"] = {"place_id": "hotel", "name": "숙소"}
        return type(request).model_validate(payload)

    def places_covered_by_jeju(self, place_ids):
        return self.covered

    def places_in_scope(self, place_ids, region_code, grid_id):
        return self.in_scope


def test_preparation_uses_jeju_boundary_without_east_scope_gate() -> None:
    """제주 경계 안 장소는 과거 동부 scope 밖이어도 전역 요청으로 준비해야 한다."""

    with pytest.raises(RequestPreparationFailure) as outside_jeju:
        RequestPreparation(PreparationFixture(covered=False)).prepare(make_request())
    assert outside_jeju.value.reason_codes == ("OUTSIDE_JEJU_SERVICE_AREA",)

    prepared = RequestPreparation(PreparationFixture(in_scope=False)).prepare(make_request())
    assert (prepared.region_code, prepared.grid_id) == ("JEJU_ALL", "ALL")
