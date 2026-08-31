"""런타임 공개 이동 미리보기 연결 테스트."""

from datetime import datetime
from pathlib import Path

from jeju_trip.domain.models import PreviewTransferInput
from jeju_trip.infrastructure.runtime_gateway import RuntimePlanningGateway
from jeju_trip.planning.policy import load_planning_policy
from tests.factories import KST


def test_transfer_preview_uses_cost_time_balance_policy(monkeypatch) -> None:
    """이동 미리보기는 생성과 같은 비용·시간 균형 정책으로 버스 대안을 비교해야 한다."""

    captured = {}

    class Gateway:
        def __init__(self, *args):
            pass

        def route(self, from_id, to_id, departure_at, strategy, request, budget):
            captured["policy"] = request.transport.selection_policy
            return None

    monkeypatch.setattr(
        "jeju_trip.infrastructure.runtime_gateway.PostgresGenerationGateway", Gateway
    )
    runtime = RuntimePlanningGateway(
        "postgresql://runtime",
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        load_planning_policy(Path("config/policies/planning_policy_v1.toml")),
    )

    runtime.preview_transfer(
        PreviewTransferInput(
            origin_place_id="place-a",
            destination_place_id="place-b",
            departure_at=datetime(2026, 8, 14, 14, 35, tzinfo=KST),
            allowed_modes={"bus", "taxi", "walk"},
        )
    )

    assert captured["policy"] == "cost_time_balance"
