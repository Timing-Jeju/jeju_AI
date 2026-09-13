"""v0.7 계약·HTTP runtime 변경 범위의 SHA-256 manifest를 생성한다."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/manifests/v07-current.sha256"
FIXED_PATHS = (
    Path("AGENTS.md"),
    Path("Dockerfile"),
    Path("README.md"),
    Path("config/data_sources.toml"),
    Path("docs/FASTAPI_MCP_CONTRACT.md"),
    Path("docs/JSON_CONTRACT.md"),
    Path("docs/PROJECT_PLAN.md"),
    Path("docs/manifests/mcp-tools-v0.7.json"),
    Path("infra/compose.private-http.yml"),
    Path("pyproject.toml"),
    Path("scripts/generate_json_schemas.py"),
    Path("scripts/generate_mcp_tool_manifest.py"),
    Path("scripts/generate_v07_checksum_manifest.py"),
    Path("scripts/generate_v07_synthetic_examples.py"),
    Path("src/jeju_trip/domain/models.py"),
    Path("src/jeju_trip/domain/durable_projection.py"),
    Path("src/jeju_trip/application/durable_projection.py"),
    Path("src/jeju_trip/interfaces/mcp/codex_launcher.py"),
    Path("src/jeju_trip/interfaces/mcp/http_launcher.py"),
    Path("src/jeju_trip/interfaces/mcp/http_server.py"),
    Path("src/jeju_trip/interfaces/mcp/server.py"),
    Path("src/jeju_trip/planning/generation.py"),
    Path("src/jeju_trip/planning/timeline_conversion.py"),
    Path("uv.lock"),
)


def _artifact_paths() -> tuple[Path, ...]:
    generated = (
        *(path.relative_to(ROOT) for path in (ROOT / "docs/contracts").glob("*.json")),
        *(
            path.relative_to(ROOT)
            for path in (ROOT / "docs/examples/v0.7/synthetic").glob("*.json")
        ),
    )
    return tuple(sorted({*FIXED_PATHS, *generated}, key=str))


def main(output: Path = DEFAULT_OUTPUT) -> None:
    lines = [
        "# v0.6은 감사 자료로 보존하고 v0.7 current 계약·HTTP runtime을 고정한 checksum이다."
    ]
    for relative in _artifact_paths():
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    main(arguments.output)
