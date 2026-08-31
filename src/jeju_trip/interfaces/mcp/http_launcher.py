"""private HTTP MCP 전용 owner-only dotenv allowlist 런처."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from jeju_trip.interfaces.mcp.codex_launcher import (
    EX_CONFIG,
    RUNTIME_KEYS,
    SYSTEM_KEYS,
    CodexMcpEnvironmentError,
    _parse_runtime_values,
    _read_secure_environment_file,
    _validate_runtime_role,
    activate_runtime_environment,
)

HTTP_KEYS = (
    "JEJU_MCP_HTTP_HOST",
    "JEJU_MCP_HTTP_PORT",
    "JEJU_MCP_PRIVATE_NETWORK_CONFIRMED",
    "JEJU_MCP_AUTH_ISSUER",
    "JEJU_MCP_AUTH_AUDIENCE",
    "JEJU_MCP_AUTH_JWKS_FILE",
    "JEJU_MCP_TLS_CERT_FILE",
    "JEJU_MCP_TLS_KEY_FILE",
)
REQUIRED_HTTP_KEYS = (
    "JEJU_MCP_AUTH_ISSUER",
    "JEJU_MCP_AUTH_AUDIENCE",
    "JEJU_MCP_AUTH_JWKS_FILE",
    "JEJU_MCP_TLS_CERT_FILE",
    "JEJU_MCP_TLS_KEY_FILE",
)


def _error(code: str) -> NoReturn:
    raise CodexMcpEnvironmentError(code)


def _validate_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        _error("MCP_HTTP_AUTH_URL_INVALID")


def load_http_runtime_environment(
    env_file: Path,
    *,
    parent_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """검증한 파일에서 runtime·bind·auth·TLS allowlist만 남긴다."""

    parent = os.environ if parent_environment is None else parent_environment
    content = _read_secure_environment_file(env_file)
    selected = _parse_runtime_values(content, allowed_keys=(*RUNTIME_KEYS, *HTTP_KEYS))
    if any(not selected.get(key, "").strip() for key in (*RUNTIME_KEYS, *REQUIRED_HTTP_KEYS)):
        _error("MCP_ENV_REQUIRED_KEY_MISSING")
    _validate_runtime_role(selected["JEJU_RUNTIME_DSN"])
    host = selected.get("JEJU_MCP_HTTP_HOST", "127.0.0.1")
    private_confirmed = selected.get("JEJU_MCP_PRIVATE_NETWORK_CONFIRMED") == "true"
    if host in {"0.0.0.0", "::", "[::]"} and not private_confirmed:
        _error("MCP_HTTP_PUBLIC_BIND_FORBIDDEN")
    port_text = selected.get("JEJU_MCP_HTTP_PORT", "8000")
    try:
        port = int(port_text)
    except ValueError:
        _error("MCP_HTTP_PORT_INVALID")
    if not 1 <= port <= 65535 or str(port) != port_text:
        _error("MCP_HTTP_PORT_INVALID")
    _validate_https_url(selected["JEJU_MCP_AUTH_ISSUER"])
    _validate_https_url(selected["JEJU_MCP_AUTH_AUDIENCE"])
    runtime = {key: parent[key] for key in SYSTEM_KEYS if parent.get(key)}
    runtime.update(selected)
    runtime.setdefault("JEJU_MCP_HTTP_HOST", "127.0.0.1")
    runtime.setdefault("JEJU_MCP_HTTP_PORT", "8000")
    return runtime


def _default_environment_file() -> Path:
    return Path(__file__).resolve().parents[4] / ".env.http"


def main() -> int:
    """환경 격리 뒤 TLS private Streamable HTTP MCP를 시작한다."""

    requested_file = os.environ.get("JEJU_MCP_HTTP_ENV_FILE")
    env_file = Path(requested_file) if requested_file else _default_environment_file()
    try:
        runtime_environment = load_http_runtime_environment(env_file)
    except CodexMcpEnvironmentError as error:
        print(error.code, file=sys.stderr)
        return EX_CONFIG
    activate_runtime_environment(runtime_environment)
    from jeju_trip.interfaces.mcp.http_server import main as server_main

    server_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
