"""MCP 외부 HTTP client 수명 관리 테스트."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jeju_trip.application.service import TripPlannerService
from jeju_trip.interfaces.mcp import server


class ClosingClient(httpx.Client):
    """ExitStack 종료 횟수를 기록하는 최소 HTTP client 대역."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def _client_factory(created: list[ClosingClient]):
    def factory(**kwargs: Any) -> ClosingClient:
        client = ClosingClient(**kwargs)
        created.append(client)
        return client

    return factory


def _runtime_environment(monkeypatch: pytest.MonkeyPatch, *, include_keys: bool = True) -> None:
    monkeypatch.setenv(
        "JEJU_RUNTIME_DSN",
        "postgresql://jeju_runtime:password@127.0.0.1:55432/jeju_trip",
    )
    if include_keys:
        monkeypatch.setenv("JEJU_TMAP_API_KEY", "tmap-fixture")
        monkeypatch.setenv("JEJU_TAGO_SERVICE_KEY", "tago-fixture")
    else:
        monkeypatch.delenv("JEJU_TMAP_API_KEY", raising=False)
        monkeypatch.delenv("JEJU_TAGO_SERVICE_KEY", raising=False)


def test_managed_service_closes_both_clients_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 MCP 종료는 TMAP·TAGO client를 각각 정확히 한 번 닫아야 한다."""

    _runtime_environment(monkeypatch)
    created: list[ClosingClient] = []
    with server.managed_service(client_factory=_client_factory(created)):
        assert len(created) == 2
    assert [client.close_calls for client in created] == [1, 1]


def test_managed_service_closes_clients_when_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """서비스 초기화 예외가 발생해도 만들어진 TMAP·TAGO client를 모두 닫아야 한다."""

    _runtime_environment(monkeypatch)
    created: list[ClosingClient] = []

    class InitializationFailure:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("safe initialization failure")

    monkeypatch.setattr(server, "TripPlannerService", InitializationFailure)
    with (
        pytest.raises(RuntimeError, match="safe initialization failure"),
        server.managed_service(client_factory=_client_factory(created)),
    ):
        pass
    assert len(created) == 2
    assert [client.close_calls for client in created] == [1, 1]


def test_managed_service_does_not_create_clients_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """외부 키가 없는 비활성 모드에서는 HTTP client를 만들지 않아야 한다."""

    _runtime_environment(monkeypatch, include_keys=False)
    created: list[ClosingClient] = []
    with server.managed_service(client_factory=_client_factory(created)):
        pass
    assert created == []


def test_managed_service_does_not_create_clients_without_runtime_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime DSN이 없으면 외부 키가 있어도 HTTP client를 만들지 않아야 한다."""

    monkeypatch.delenv("JEJU_RUNTIME_DSN", raising=False)
    monkeypatch.setenv("JEJU_TMAP_API_KEY", "tmap-fixture")
    monkeypatch.setenv("JEJU_TAGO_SERVICE_KEY", "tago-fixture")
    created: list[ClosingClient] = []
    with server.managed_service(client_factory=_client_factory(created)):
        pass
    assert created == []


def test_injected_service_does_not_create_external_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """테스트용 service 주입 경로는 외부 HTTP client를 생성하지 않아야 한다."""

    _runtime_environment(monkeypatch)
    created: list[ClosingClient] = []
    monkeypatch.setattr(server.httpx, "Client", _client_factory(created))
    server.create_server(service=TripPlannerService())
    assert created == []
