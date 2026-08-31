"""private HTTP MCP 전용 allowlist 런처 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from jeju_trip.interfaces.mcp.codex_launcher import CodexMcpEnvironmentError
from jeju_trip.interfaces.mcp.http_launcher import load_http_runtime_environment


def _write_env(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _valid_http_env(tmp_path: Path, *extra_lines: str) -> str:
    return "\n".join(
        (
            "JEJU_RUNTIME_DSN=postgresql://jeju_runtime:fixture@127.0.0.1:55432/jeju_trip",
            "JEJU_TMAP_API_KEY=fixture-tmap",
            "JEJU_TAGO_SERVICE_KEY=fixture-tago",
            "JEJU_MCP_HTTP_HOST=127.0.0.1",
            "JEJU_MCP_HTTP_PORT=8443",
            "JEJU_MCP_AUTH_ISSUER=https://issuer.internal.example",
            "JEJU_MCP_AUTH_AUDIENCE=https://mcp.internal.example/mcp",
            f"JEJU_MCP_AUTH_JWKS_FILE={tmp_path / 'jwks.json'}",
            f"JEJU_MCP_TLS_CERT_FILE={tmp_path / 'server.crt'}",
            f"JEJU_MCP_TLS_KEY_FILE={tmp_path / 'server.key'}",
            *extra_lines,
            "",
        )
    )


def test_http_launcher_keeps_only_runtime_auth_bind_and_tls_values(tmp_path: Path) -> None:
    """HTTP 런타임은 승인된 실행·인증·TLS 값과 최소 시스템 환경만 보존해야 한다."""

    env_file = _write_env(
        tmp_path / ".env.http",
        _valid_http_env(
            tmp_path,
            "JEJU_IMPORTER_DSN=forbidden-importer",
            "OPENAI_API_KEY=forbidden-openai",
        ),
    )
    parent = {
        "HOME": "/safe-home",
        "LANG": "ko_KR.UTF-8",
        "HTTPS_PROXY": "http://forbidden-proxy.invalid",
        "PYTHONPATH": "/forbidden/python",
        "CODEX_INTERNAL_TOKEN": "forbidden-codex",
    }

    runtime = load_http_runtime_environment(env_file, parent_environment=parent)

    assert runtime["HOME"] == "/safe-home"
    assert runtime["JEJU_MCP_HTTP_HOST"] == "127.0.0.1"
    assert runtime["JEJU_MCP_HTTP_PORT"] == "8443"
    assert runtime["JEJU_MCP_AUTH_ISSUER"] == "https://issuer.internal.example"
    assert runtime["JEJU_MCP_TLS_KEY_FILE"].endswith("server.key")
    assert not {
        "JEJU_IMPORTER_DSN",
        "OPENAI_API_KEY",
        "HTTPS_PROXY",
        "PYTHONPATH",
        "CODEX_INTERNAL_TOKEN",
    } & runtime.keys()


@pytest.mark.parametrize(
    ("line", "code"),
    (
        ("JEJU_MCP_HTTP_HOST=0.0.0.0", "MCP_HTTP_PUBLIC_BIND_FORBIDDEN"),
        ("JEJU_MCP_HTTP_PORT=0", "MCP_HTTP_PORT_INVALID"),
        ("JEJU_MCP_AUTH_ISSUER=http://issuer.invalid", "MCP_HTTP_AUTH_URL_INVALID"),
    ),
    ids=("공개 bind", "잘못된 port", "비 TLS issuer"),
)
def test_http_launcher_rejects_unsafe_network_configuration(
    tmp_path: Path, line: str, code: str
) -> None:
    """공개 bind와 잘못된 port·인증 URL은 서버 import 전에 거부해야 한다."""

    base_lines = [
        item
        for item in _valid_http_env(tmp_path).splitlines()
        if not item.startswith(line.split("=", 1)[0] + "=")
    ]
    env_file = _write_env(tmp_path / ".env.http", "\n".join((*base_lines, line, "")))

    with pytest.raises(CodexMcpEnvironmentError, match=code):
        load_http_runtime_environment(env_file)
