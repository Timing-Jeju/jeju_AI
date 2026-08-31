"""OpenAI primary/fallback 호출 경계 테스트."""

from __future__ import annotations

from jeju_trip.agents.model_router import invoke_with_single_fallback


def test_provider_timeout_calls_fallback_only_once() -> None:
    """primary 모델 timeout 후 fallback 모델을 정확히 한 번만 호출해야 한다."""

    calls = {"primary": 0, "fallback": 0}

    def primary() -> str:
        calls["primary"] += 1
        raise TimeoutError

    def fallback() -> str:
        calls["fallback"] += 1
        return "grounded-output"

    result = invoke_with_single_fallback(primary, fallback, (TimeoutError,))
    assert result == "grounded-output"
    assert calls == {"primary": 1, "fallback": 1}
