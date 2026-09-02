"""FastMCP가 노출하는 여섯 도구 schema fingerprint manifest를 생성한다."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from jeju_trip.domain.models import SCHEMA_VERSION
from jeju_trip.interfaces.mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/manifests/mcp-tools-v0.7.json"


def _canonical_sha256(value: dict[str, Any]) -> str:
    rendered = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


async def _manifest() -> dict[str, Any]:
    tools = sorted(await create_server().list_tools(), key=lambda item: item.name)
    return {
        "contractVersion": SCHEMA_VERSION,
        "transportParity": ["stdio", "streamable-http"],
        "toolCount": len(tools),
        "tools": [
            {
                "name": tool.name,
                "inputSchemaSha256": _canonical_sha256(tool.inputSchema),
                "outputSchemaSha256": _canonical_sha256(tool.outputSchema or {}),
            }
            for tool in tools
        ],
    }


def main(output: Path = DEFAULT_OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(asyncio.run(_manifest()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    main(arguments.output)
