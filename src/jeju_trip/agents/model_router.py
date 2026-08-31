"""환경 주입 모델의 timeout 후 fallback을 한 번만 허용하는 router."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAISettings:
    primary_model: str
    secondary_model: str
    timeout_seconds: float
    api_key: str

    @classmethod
    def from_environment(cls) -> OpenAISettings:
        required = {
            name: os.getenv(name)
            for name in (
                "JEJU_OPENAI_API_KEY",
                "JEJU_OPENAI_PRIMARY_MODEL",
                "JEJU_OPENAI_SECONDARY_MODEL",
                "JEJU_OPENAI_TIMEOUT_SECONDS",
            )
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"OPENAI_SETTINGS_MISSING:{','.join(missing)}")
        return cls(
            api_key=required["JEJU_OPENAI_API_KEY"] or "",
            primary_model=required["JEJU_OPENAI_PRIMARY_MODEL"] or "",
            secondary_model=required["JEJU_OPENAI_SECONDARY_MODEL"] or "",
            timeout_seconds=float(required["JEJU_OPENAI_TIMEOUT_SECONDS"] or "0"),
        )


def invoke_with_single_fallback[T](
    primary: Callable[[], T], fallback: Callable[[], T], retryable: tuple[type[Exception], ...]
) -> T:
    try:
        return primary()
    except retryable:
        return fallback()
