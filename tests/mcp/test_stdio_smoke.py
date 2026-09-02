"""실제 stdio transport MCP 종단 smoke 테스트."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.factories import make_request

ROOT = Path(__file__).resolve().parents[2]


def _wire_arguments() -> dict[str, object]:
    arguments: dict[str, object] = {
        "requestId": "request-stdio-0001",
        "request": make_request().model_dump(mode="json"),
    }
    arguments["inputHash"] = hashlib.sha256(
        json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return arguments


@pytest.mark.asyncio
async def test_stdio_mcp_lists_and_calls_structured_tool(tmp_path: Path) -> None:
    """stdio MCP가 handshake 후 여섯 도구와 구조화 추천 실패 응답을 반환해야 한다."""

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jeju_trip.interfaces.mcp.server"],
    )
    stderr_path = tmp_path / "mcp-stderr.log"
    with stderr_path.open("w+", encoding="utf-8") as stderr:
        async with (
            stdio_client(parameters, errlog=stderr) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 6
            result = await session.call_tool(
                "recommend_jeju_day_trips",
                _wire_arguments(),
            )
        stderr.seek(0)
        stderr_text = stderr.read()
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "insufficient_feasible_routes"
    assert "Traceback" not in stderr_text


@pytest.mark.asyncio
async def test_secure_codex_entrypoint_completes_stdio_handshake(tmp_path: Path) -> None:
    """Codex 보안 entrypoint가 실제 stdio handshake와 여섯 도구 노출을 완료해야 한다."""

    env_file = tmp_path / ".env"
    secrets = ("stdio-runtime-secret", "stdio-tmap-secret", "stdio-tago-secret")
    env_file.write_text(
        "\n".join(
            (
                (
                    "JEJU_RUNTIME_DSN=postgresql://jeju_runtime:"
                    f"{secrets[0]}@127.0.0.1:55432/jeju_trip"
                ),
                f"JEJU_TMAP_API_KEY={secrets[1]}",
                f"JEJU_TAGO_SERVICE_KEY={secrets[2]}",
            )
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    uv = shutil.which("uv")
    assert uv is not None
    parameters = StdioServerParameters(
        command=uv,
        args=["run", "--frozen", "--no-env-file", "jeju-trip-mcp-codex"],
        cwd=ROOT,
        env={**os.environ, "JEJU_MCP_ENV_FILE": str(env_file)},
    )
    stderr_path = tmp_path / "codex-mcp-stderr.log"
    with stderr_path.open("w+", encoding="utf-8") as stderr:
        async with (
            stdio_client(parameters, errlog=stderr) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
        stderr.seek(0)
        stderr_text = stderr.read()

    assert len(tools.tools) == 6
    assert all(tool.inputSchema and tool.outputSchema for tool in tools.tools)
    assert "Traceback" not in stderr_text
    assert all(secret not in stderr_text for secret in secrets)
