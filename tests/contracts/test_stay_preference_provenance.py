"""서버가 선택한 체류 정책과 사용자 고정 시간을 구분하는 공개 계약."""

import pytest
from pydantic import ValidationError

from jeju_trip.domain.models import PlaceDurationPreference


def test_existing_duration_defaults_to_user_requested() -> None:
    """기존 체류시간 입력은 사용자 고정 제약으로 호환해야 한다."""
    value = PlaceDurationPreference(place_id="tourapi.place:1", requested_stay_minutes=90)
    assert value.source == "user_requested"
    assert value.policy_version is None


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("place_override", id="장소별_정책"),
        pytest.param("category_default", id="분류별_정책"),
    ],
)
def test_server_duration_keeps_policy_provenance(source: str) -> None:
    """서버 추천값은 고정 제약의 출처와 정책 버전 및 시행시각을 보존해야 한다."""
    value = PlaceDurationPreference.model_validate(
        {
            "place_id": "tourapi.place:1",
            "requested_stay_minutes": 75,
            "source": source,
            "policy_version": "stay-v1",
            "policy_effective_at": "2026-09-01T00:00:00Z",
        }
    )
    assert value.requested_stay_minutes == 75
    assert value.source == source
    assert value.policy_version == "stay-v1"
    assert value.policy_effective_at is not None


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"source": "category_default"}, id="정책_출처_누락"),
        pytest.param(
            {"source": "user_requested", "policy_version": "stay-v1"}, id="사용자_정책_혼합"
        ),
        pytest.param(
            {
                "source": "place_override",
                "policy_version": "원문 정책",
                "policy_effective_at": "2026-09-01T00:00:00Z",
            },
            id="비정상_버전",
        ),
        pytest.param(
            {
                "source": "place_override",
                "policy_version": "stay-v1",
                "policy_effective_at": "2026-09-01T00:00:00",
            },
            id="시간대_누락",
        ),
    ],
)
def test_invalid_duration_provenance_is_rejected(metadata: dict[str, str]) -> None:
    """누락되거나 사용자 값과 혼합된 정책 출처를 거부해야 한다."""
    with pytest.raises(ValidationError):
        PlaceDurationPreference.model_validate(
            {
                "place_id": "tourapi.place:1",
                "requested_stay_minutes": 75,
                **metadata,
            }
        )
