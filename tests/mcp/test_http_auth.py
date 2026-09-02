"""private Streamable HTTP MCP의 RS256 인증과 schema 동등성 테스트."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.testclient import TestClient

from jeju_trip.interfaces.mcp.http_server import (
    HTTP_REQUIRED_SCOPE,
    HttpServerSettings,
    Rs256JwksTokenVerifier,
    create_http_server,
)
from jeju_trip.interfaces.mcp.server import create_server


def _wire_arguments(request: dict[str, Any]) -> dict[str, Any]:
    arguments = {"requestId": "request-0001", "request": request}
    arguments["inputHash"] = hashlib.sha256(
        json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return arguments


def _write_jwks(path: Path, private_key: rsa.RSAPrivateKey, kid: str) -> None:
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    path.write_text(json.dumps({"keys": [public_jwk]}), encoding="utf-8")


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    issuer: str = "https://issuer.internal.example",
    audience: str = "https://mcp.internal.example/mcp",
    scope: str = HTTP_REQUIRED_SCOPE,
    lifetime_seconds: int = 120,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": "spring-worker",
            "iat": now,
            "exp": now + timedelta(seconds=lifetime_seconds),
            "jti": "fixture-jti",
            "scope": scope,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


@pytest.mark.asyncio
async def test_rs256_verifier_accepts_expected_issuer_audience_scope_and_jti(
    tmp_path: Path,
) -> None:
    """정확한 issuer·audience·scope·만료·JTI를 가진 RS256 token만 허용해야 한다."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, private_key, "key-1")
    verifier = Rs256JwksTokenVerifier(
        issuer="https://issuer.internal.example",
        audience="https://mcp.internal.example/mcp",
        jwks_path=jwks_path,
        required_scope=HTTP_REQUIRED_SCOPE,
    )

    access = await verifier.verify_token(_token(private_key, kid="key-1"))

    assert access is not None
    assert access.client_id == "spring-worker"
    assert access.scopes == [HTTP_REQUIRED_SCOPE]
    assert access.claims is not None
    assert access.claims["jti"] == "fixture-jti"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "value"),
    (
        ("issuer", "https://wrong-issuer.internal.example"),
        ("audience", "https://wrong-audience.internal.example/mcp"),
        ("scope", "wrong:scope"),
        ("lifetime_seconds", -1),
        ("lifetime_seconds", 301),
    ),
    ids=("issuer 불일치", "audience 불일치", "scope 누락", "만료", "과도한 수명"),
)
async def test_rs256_verifier_rejects_invalid_service_token(
    tmp_path: Path, claim: str, value: str | int
) -> None:
    """잘못된 인증 claim이나 5분을 넘는 service token은 비밀 노출 없이 거부해야 한다."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, private_key, "key-1")
    verifier = Rs256JwksTokenVerifier(
        issuer="https://issuer.internal.example",
        audience="https://mcp.internal.example/mcp",
        jwks_path=jwks_path,
        required_scope=HTTP_REQUIRED_SCOPE,
    )
    arguments: dict[str, Any] = {claim: value}

    assert await verifier.verify_token(_token(private_key, kid="key-1", **arguments)) is None


@pytest.mark.asyncio
async def test_rs256_verifier_reloads_rotated_jwks(tmp_path: Path) -> None:
    """JWKS 파일을 원자 교체하면 재시작 없이 새 kid를 검증해야 한다."""

    first_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, first_key, "key-1")
    verifier = Rs256JwksTokenVerifier(
        issuer="https://issuer.internal.example",
        audience="https://mcp.internal.example/mcp",
        jwks_path=jwks_path,
        required_scope=HTTP_REQUIRED_SCOPE,
    )
    assert await verifier.verify_token(_token(first_key, kid="key-1")) is not None

    replacement = tmp_path / "jwks.next.json"
    _write_jwks(replacement, second_key, "key-2")
    replacement.replace(jwks_path)

    assert await verifier.verify_token(_token(second_key, kid="key-2")) is not None
    assert await verifier.verify_token(_token(first_key, kid="key-1")) is None


@pytest.mark.asyncio
async def test_stdio_and_http_expose_identical_six_tool_schemas(tmp_path: Path) -> None:
    """stdio와 private HTTP는 여섯 도구 이름과 입출력 schema가 정확히 같아야 한다."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, private_key, "key-1")
    settings = HttpServerSettings(
        host="127.0.0.1",
        port=8765,
        issuer="https://issuer.internal.example",
        audience="https://mcp.internal.example/mcp",
        jwks_path=jwks_path,
        tls_cert_path=tmp_path / "server.crt",
        tls_key_path=tmp_path / "server.key",
    )

    stdio_tools = await create_server().list_tools()
    http_tools = await create_http_server(settings=settings).list_tools()

    assert [tool.model_dump() for tool in http_tools] == [tool.model_dump() for tool in stdio_tools]


def test_http_requires_bearer_and_supports_initialize_list_and_call(tmp_path: Path) -> None:
    """private HTTP는 인증 뒤 initialize·tools/list·tools/call을 JSON 응답으로 처리해야 한다."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks_path = tmp_path / "jwks.json"
    _write_jwks(jwks_path, private_key, "key-1")
    settings = HttpServerSettings(
        host="127.0.0.1",
        port=8765,
        issuer="https://issuer.internal.example",
        audience="https://mcp.internal.example/mcp",
        jwks_path=jwks_path,
        tls_cert_path=tmp_path / "server.crt",
        tls_key_path=tmp_path / "server.key",
    )
    app = create_http_server(settings=settings).streamable_http_app()
    common_headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    authenticated_headers = {
        **common_headers,
        "authorization": f"Bearer {_token(private_key, kid='key-1')}",
    }

    with TestClient(app, base_url="https://mcp.internal.example") as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json()["contractVersion"] == "0.7.0"
        assert (
            client.post(
                "/mcp",
                headers=common_headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            ).status_code
            == 401
        )

        initialized = client.post(
            "/mcp",
            headers=authenticated_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "spring-fixture", "version": "1"},
                },
            },
        )
        listed = client.post(
            "/mcp",
            headers=authenticated_headers,
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
        )
        called = client.post(
            "/mcp",
            headers=authenticated_headers,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "inspect_jeju_bus_stop",
                    "arguments": _wire_arguments({"stop_id": "fixture-stop"}),
                },
            },
        )

    assert initialized.status_code == 200
    assert initialized.json()["result"]["protocolVersion"]
    assert listed.status_code == 200
    assert len(listed.json()["result"]["tools"]) == 6
    assert called.status_code == 200
    assert called.json()["result"]["structuredContent"]["status"] == "data_unavailable"
