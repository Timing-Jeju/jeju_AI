"""PR 품질 게이트가 로컬 불변조건과 격리 서비스 경계를 유지하는지 검사한다."""

from pathlib import Path

from scripts.start_ci_services import COMPOSE, SERVICES

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/quality.yml"


def test_ci_runs_required_quality_commands_in_order() -> None:
    """CI는 한글 설명·Ruff·Pyright·pytest를 정해진 순서로 모두 실행해야 한다."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    commands = (
        "uv run python scripts/check_korean_test_descriptions.py",
        "uv run ruff check .",
        "uv run pyright",
        "uv run pytest",
    )

    assert all(command in workflow for command in commands)
    assert tuple(workflow.index(command) for command in commands) == tuple(
        sorted(workflow.index(command) for command in commands)
    )


def test_ci_does_not_combine_frozen_environment_with_locked_sync() -> None:
    """CI는 최신 uv가 거부하는 UV_FROZEN과 --locked 중복 조합을 사용하지 않아야 한다."""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert not ("UV_FROZEN" in workflow and "uv sync --locked" in workflow)


def test_ci_live_tests_use_only_ephemeral_service_endpoints() -> None:
    """CI live 회귀는 개발 DB·MinIO가 아니라 test profile의 명시된 endpoint만 사용해야 한다."""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "jeju_trip_test" in workflow
    assert "127.0.0.1:55433" in workflow
    assert "http://127.0.0.1:59010" in workflow
    assert "uv run python scripts/start_ci_services.py" in workflow
    assert COMPOSE == [
        "docker", "compose", "--parallel", "1", "-f", "infra/compose.local.yml",
        "--profile", "test",
    ]
    assert SERVICES == ["postgres-test", "minio-test"]
    assert "if: always()" in workflow
    assert "--profile test down" in workflow
    assert "tests/integration" in workflow
