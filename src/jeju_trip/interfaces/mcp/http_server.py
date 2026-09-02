"""인증된 private stateless Streamable HTTP MCP server."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jwt
import uvicorn
from jwt.algorithms import RSAAlgorithm
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse

from jeju_trip.application.service import TripPlannerService
from jeju_trip.interfaces.mcp.server import create_server, managed_service

HTTP_REQUIRED_SCOPE = "jeju:mcp:invoke"
MAX_SERVICE_TOKEN_LIFETIME_SECONDS = 300
MAX_JWKS_BYTES = 1024 * 1024


@dataclass(frozen=True)
class HttpServerSettings:
    """비밀값을 포함하지 않는 private HTTP bind·인증·TLS 설정."""

    host: str
    port: int
    issuer: str
    audience: str
    jwks_path: Path
    tls_cert_path: Path
    tls_key_path: Path
    required_scope: str = HTTP_REQUIRED_SCOPE

    @classmethod
    def from_environment(cls) -> HttpServerSettings:
        return cls(
            host=os.getenv("JEJU_MCP_HTTP_HOST", "127.0.0.1"),
            port=int(os.getenv("JEJU_MCP_HTTP_PORT", "8000")),
            issuer=os.environ["JEJU_MCP_AUTH_ISSUER"],
            audience=os.environ["JEJU_MCP_AUTH_AUDIENCE"],
            jwks_path=Path(os.environ["JEJU_MCP_AUTH_JWKS_FILE"]),
            tls_cert_path=Path(os.environ["JEJU_MCP_TLS_CERT_FILE"]),
            tls_key_path=Path(os.environ["JEJU_MCP_TLS_KEY_FILE"]),
        )

    def validate_runtime_files(self) -> None:
        for path in (self.jwks_path, self.tls_cert_path, self.tls_key_path):
            if not path.is_file():
                raise ValueError("MCP_HTTP_RUNTIME_FILE_MISSING")

    @property
    def allowed_hosts(self) -> list[str]:
        values = {self.host, f"{self.host}:{self.port}"}
        audience_host = urlsplit(self.audience).netloc
        if audience_host:
            values.add(audience_host)
        return sorted(values)


class Rs256JwksTokenVerifier:
    """배포된 local JWKS를 rotation-safe하게 다시 읽는 service JWT verifier."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_path: Path,
        required_scope: str,
        maximum_lifetime_seconds: int = MAX_SERVICE_TOKEN_LIFETIME_SECONDS,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_path = jwks_path
        self._required_scope = required_scope
        self._maximum_lifetime_seconds = maximum_lifetime_seconds
        self._file_identity: tuple[int, int, int, int] | None = None
        self._keys: dict[str, Any] = {}

    def _load_keys(self) -> dict[str, Any]:
        file_stat = self._jwks_path.stat()
        identity = (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_mtime_ns,
            file_stat.st_size,
        )
        if identity == self._file_identity:
            return self._keys
        if file_stat.st_size <= 0 or file_stat.st_size > MAX_JWKS_BYTES:
            raise ValueError("MCP_AUTH_JWKS_SIZE_INVALID")
        document = json.loads(self._jwks_path.read_text(encoding="utf-8"))
        values = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(values, list) or not values:
            raise ValueError("MCP_AUTH_JWKS_INVALID")
        parsed: dict[str, Any] = {}
        for item in values:
            if not isinstance(item, dict):
                raise ValueError("MCP_AUTH_JWKS_INVALID")
            kid = item.get("kid")
            if (
                not isinstance(kid, str)
                or not kid
                or item.get("kty") != "RSA"
                or item.get("alg") not in {None, "RS256"}
                or item.get("use") not in {None, "sig"}
                or kid in parsed
            ):
                raise ValueError("MCP_AUTH_JWKS_INVALID")
            parsed[kid] = RSAAlgorithm.from_jwk(json.dumps(item))
        self._keys = parsed
        self._file_identity = identity
        return parsed

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str):
                return None
            key = self._load_keys().get(kid)
            if key is None:
                return None
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=5,
                options={"require": ["iss", "aud", "sub", "iat", "exp", "jti"]},
            )
            now = int(time.time())
            issued_at = int(claims["iat"])
            expires_at = int(claims["exp"])
            subject = claims["sub"]
            jti = claims["jti"]
            scope_claim = claims.get("scope", "")
            scopes = scope_claim.split() if isinstance(scope_claim, str) else []
            if (
                not isinstance(subject, str)
                or not subject
                or not isinstance(jti, str)
                or not 1 <= len(jti) <= 128
                or issued_at > now + 5
                or expires_at <= now
                or expires_at - issued_at > self._maximum_lifetime_seconds
                or self._required_scope not in scopes
            ):
                return None
            return AccessToken(
                token="<redacted>",
                client_id=subject,
                subject=subject,
                scopes=scopes,
                expires_at=expires_at,
                resource=self._audience,
                claims={
                    "iss": claims["iss"],
                    "aud": claims["aud"],
                    "sub": subject,
                    "iat": issued_at,
                    "exp": expires_at,
                    "jti": jti,
                },
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, jwt.PyJWTError):
            return None


def create_http_server(
    service: TripPlannerService | None = None,
    *,
    settings: HttpServerSettings,
) -> FastMCP:
    """stdio와 같은 여섯 도구를 인증된 stateless HTTP로 노출한다."""

    verifier = Rs256JwksTokenVerifier(
        issuer=settings.issuer,
        audience=settings.audience,
        jwks_path=settings.jwks_path,
        required_scope=settings.required_scope,
    )
    server = create_server(
        service,
        fastmcp_options={
            "host": settings.host,
            "port": settings.port,
            "streamable_http_path": "/mcp",
            "json_response": True,
            "stateless_http": True,
            "token_verifier": verifier,
            "auth": AuthSettings(
                issuer_url=AnyHttpUrl(settings.issuer),
                resource_server_url=AnyHttpUrl(settings.audience),
                required_scopes=[settings.required_scope],
            ),
            "transport_security": TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=settings.allowed_hosts,
                allowed_origins=[],
            ),
        },
    )

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @server.custom_route("/ready", methods=["GET"])
    async def ready(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ready", "contractVersion": "0.7.0"})

    return server


def main() -> None:
    """TLS가 적용된 private HTTP process에서 service lifecycle을 소유한다."""

    settings = HttpServerSettings.from_environment()
    settings.validate_runtime_files()
    with managed_service() as service:
        app = create_http_server(service, settings=settings).streamable_http_app()
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
            ssl_certfile=str(settings.tls_cert_path),
            ssl_keyfile=str(settings.tls_key_path),
            access_log=False,
            server_header=False,
        )


if __name__ == "__main__":
    main()
