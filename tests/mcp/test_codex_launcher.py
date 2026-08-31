"""Codex MCP 전용 런처의 비밀값·환경 파일 경계 테스트."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jeju_trip.interfaces.mcp.codex_launcher import (
    CodexMcpEnvironmentError,
    activate_runtime_environment,
    load_runtime_environment,
    main,
)

RUNTIME_DSN = "postgresql://jeju_runtime:runtime-password@127.0.0.1:55432/jeju_trip"
TMAP_SECRET = "fixture-tmap-secret"
TAGO_SECRET = "fixture-tago-secret"


def _write_env(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _valid_env(*extra_lines: str) -> str:
    lines = (
        f"JEJU_RUNTIME_DSN='{RUNTIME_DSN}'",
        f"JEJU_TMAP_API_KEY={TMAP_SECRET}",
        f'JEJU_TAGO_SERVICE_KEY="{TAGO_SECRET}"',
        *extra_lines,
    )
    return "\n".join(lines) + "\n"


def test_runtime_environment_keeps_only_allowlisted_values(tmp_path: Path) -> None:
    """런타임 환경에는 세 비밀값과 승인된 최소 시스템 변수만 남아야 한다."""

    env_file = _write_env(
        tmp_path / ".env",
        _valid_env(
            "JEJU_IMPORTER_DSN=importer-secret",
            "JEJU_MIGRATOR_DSN=migrator-secret",
            "JEJU_TOURAPI_SERVICE_KEY=tourapi-secret",
            "JEJU_OPENAI_API_KEY=openai-secret",
            "JEJU_RAW_S3_SECRET_KEY=s3-secret",
        ),
    )
    parent = {
        "HOME": "/safe-home",
        "LANG": "ko_KR.UTF-8",
        "AWS_SECRET_ACCESS_KEY": "parent-aws-secret",
        "PYTHONPATH": "/untrusted/python",
        "HTTPS_PROXY": "http://proxy-user:proxy-password@example.invalid",
        "JEJU_MCP_ENV_FILE": str(env_file),
        "JEJU_IMPORTER_DSN": "parent-importer-secret",
        "CODEX_INTERNAL_TOKEN": "codex-secret",
    }

    runtime = load_runtime_environment(env_file, parent_environment=parent)

    assert runtime == {
        "HOME": "/safe-home",
        "LANG": "ko_KR.UTF-8",
        "JEJU_RUNTIME_DSN": RUNTIME_DSN,
        "JEJU_TMAP_API_KEY": TMAP_SECRET,
        "JEJU_TAGO_SERVICE_KEY": TAGO_SECRET,
    }


def test_parent_forbidden_values_are_removed_before_server_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """부모 프로세스의 금지 변수가 있어도 실제 서버 환경에서는 제거돼야 한다."""

    env_file = _write_env(tmp_path / ".env", _valid_env())
    parent = {
        "HOME": "/safe-home",
        "JEJU_IMPORTER_DSN": "parent-importer-secret",
        "JEJU_RAW_S3_ACCESS_KEY": "parent-s3-secret",
        "OPENAI_API_KEY": "parent-openai-secret",
        "PYTHONPATH": "/untrusted/python",
        "JEJU_MCP_ENV_FILE": str(env_file),
    }
    runtime = load_runtime_environment(env_file, parent_environment=parent)

    with monkeypatch.context() as context:
        context.setattr(os, "environ", dict(parent))
        activate_runtime_environment(runtime)
        assert dict(os.environ) == runtime


def test_launcher_error_does_not_print_fixture_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """런처 실패 stderr에는 env 파일의 비밀값이나 원문이 포함되지 않아야 한다."""

    env_file = _write_env(
        tmp_path / ".env",
        _valid_env("JEJU_RUNTIME_DSN=postgresql://jeju_admin:admin-secret@localhost/db"),
    )
    monkeypatch.setenv("JEJU_MCP_ENV_FILE", str(env_file))

    assert main() == 78
    stderr = capsys.readouterr().err
    assert "MCP_RUNTIME_DSN_PRIVILEGED_ROLE_FORBIDDEN" in stderr
    for secret in (TMAP_SECRET, TAGO_SECRET, "admin-secret", RUNTIME_DSN):
        assert secret not in stderr


def test_missing_environment_file_is_rejected(tmp_path: Path) -> None:
    """존재하지 않는 env 파일은 안정적인 오류 코드로 거부해야 한다."""

    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_FILE_MISSING"):
        load_runtime_environment(tmp_path / "missing.env")


@pytest.mark.parametrize(
    "target_name",
    ("directory", "/dev/null"),
    ids=("디렉터리", "문자 장치"),
)
def test_non_regular_environment_file_is_rejected(tmp_path: Path, target_name: str) -> None:
    """디렉터리나 장치 파일은 env 파일로 열 수 없어야 한다."""

    target = tmp_path / target_name if target_name == "directory" else Path(target_name)
    if target_name == "directory":
        target.mkdir()
    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_FILE_NOT_REGULAR"):
        load_runtime_environment(target)


def test_environment_file_owned_by_another_uid_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """현재 사용자와 소유 UID가 다른 env 파일은 시작 전에 거부해야 한다."""

    env_file = _write_env(tmp_path / ".env", _valid_env())
    monkeypatch.setattr(os, "getuid", lambda: env_file.stat().st_uid + 1)

    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_FILE_WRONG_OWNER"):
        load_runtime_environment(env_file)


@pytest.mark.parametrize(
    "mode",
    (0o644, 0o660, 0o666),
    ids=("그룹 읽기 허용", "그룹 쓰기 허용", "전체 쓰기 허용"),
)
def test_environment_file_with_group_or_other_permissions_is_rejected(
    tmp_path: Path, mode: int
) -> None:
    """group 또는 other 권한 비트가 있는 env 파일은 모두 거부해야 한다."""

    env_file = _write_env(tmp_path / ".env", _valid_env(), mode)
    with pytest.raises(
        CodexMcpEnvironmentError, match="MCP_ENV_FILE_PERMISSIONS_TOO_OPEN"
    ):
        load_runtime_environment(env_file)


def test_owner_only_environment_file_is_accepted(tmp_path: Path) -> None:
    """현재 사용자 소유의 0600 regular env 파일은 허용해야 한다."""

    env_file = _write_env(tmp_path / ".env", _valid_env(), 0o600)
    assert load_runtime_environment(env_file)["JEJU_RUNTIME_DSN"] == RUNTIME_DSN


def test_environment_file_larger_than_limit_is_rejected(tmp_path: Path) -> None:
    """64 KiB를 넘는 env 파일은 내용을 파싱하기 전에 거부해야 한다."""

    env_file = _write_env(tmp_path / ".env", "#" * (64 * 1024 + 1))
    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_FILE_TOO_LARGE"):
        load_runtime_environment(env_file)


def test_non_utf8_environment_file_is_rejected(tmp_path: Path) -> None:
    """UTF-8이 아닌 env 파일은 비밀값을 출력하지 않고 거부해야 한다."""

    env_file = tmp_path / ".env"
    env_file.write_bytes(b"JEJU_RUNTIME_DSN=\xff")
    env_file.chmod(0o600)
    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_FILE_INVALID_UTF8"):
        load_runtime_environment(env_file)


@pytest.mark.parametrize(
    "invalid_line",
    ("NOT_AN_ASSIGNMENT", "JEJU_TMAP_API_KEY='unfinished"),
    ids=("할당식 아님", "따옴표 미완성"),
)
def test_invalid_dotenv_syntax_is_rejected(tmp_path: Path, invalid_line: str) -> None:
    """잘못된 할당식과 미완성 따옴표는 제한된 dotenv 문법에서 거부해야 한다."""

    env_file = _write_env(tmp_path / ".env", _valid_env(invalid_line))
    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_SYNTAX_INVALID"):
        load_runtime_environment(env_file)


def test_plain_single_double_and_export_values_are_parsed(tmp_path: Path) -> None:
    """plain·single·double quote와 선택적 export 선언을 정확히 읽어야 한다."""

    env_file = _write_env(
        tmp_path / ".env",
        "\n".join(
            (
                "# 안전한 fixture",
                f"export JEJU_RUNTIME_DSN='{RUNTIME_DSN}'",
                f'JEJU_TMAP_API_KEY="{TMAP_SECRET}"',
                f"JEJU_TAGO_SERVICE_KEY={TAGO_SECRET}",
                "",
            )
        ),
    )
    runtime = load_runtime_environment(env_file)
    assert runtime["JEJU_RUNTIME_DSN"] == RUNTIME_DSN
    assert runtime["JEJU_TMAP_API_KEY"] == TMAP_SECRET
    assert runtime["JEJU_TAGO_SERVICE_KEY"] == TAGO_SECRET


def test_duplicate_required_key_uses_last_declaration(tmp_path: Path) -> None:
    """중복 런타임 DSN은 기존 env 파일 호환을 위해 마지막 선언을 사용해야 한다."""

    env_file = _write_env(
        tmp_path / ".env",
        _valid_env(f"JEJU_RUNTIME_DSN='{RUNTIME_DSN}?application_name=last'"),
    )
    assert load_runtime_environment(env_file)["JEJU_RUNTIME_DSN"].endswith(
        "application_name=last"
    )


@pytest.mark.parametrize(
    "dangerous_value",
    ("$RUNTIME_DSN", "${RUNTIME_DSN}", "$(printf injected)", "`printf injected`"),
    ids=("달러 변수", "중괄호 변수", "명령 치환", "백틱 치환"),
)
def test_shell_expansion_syntax_is_never_evaluated(
    tmp_path: Path, dangerous_value: str
) -> None:
    """변수·명령 치환 문법은 실행하지 않고 구문 오류로 거부해야 한다."""

    env_file = _write_env(
        tmp_path / ".env",
        _valid_env(f"JEJU_TMAP_API_KEY={dangerous_value}"),
    )
    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_SYNTAX_INVALID"):
        load_runtime_environment(env_file)


@pytest.mark.parametrize(
    "key",
    ("JEJU_RUNTIME_DSN", "JEJU_TMAP_API_KEY", "JEJU_TAGO_SERVICE_KEY"),
    ids=("런타임 DSN", "TMAP 키", "TAGO 키"),
)
def test_missing_or_empty_required_value_is_rejected(tmp_path: Path, key: str) -> None:
    """필수 세 키 중 하나라도 누락되거나 비어 있으면 fail-closed 해야 한다."""

    lines = {
        "JEJU_RUNTIME_DSN": RUNTIME_DSN,
        "JEJU_TMAP_API_KEY": TMAP_SECRET,
        "JEJU_TAGO_SERVICE_KEY": TAGO_SECRET,
    }
    lines[key] = ""
    env_file = _write_env(
        tmp_path / ".env", "\n".join(f"{name}={value}" for name, value in lines.items())
    )
    with pytest.raises(CodexMcpEnvironmentError, match="MCP_ENV_REQUIRED_KEY_MISSING"):
        load_runtime_environment(env_file)


@pytest.mark.parametrize(
    "role",
    ("jeju_importer", "jeju_migrator", "jeju_admin", "postgres", "root"),
    ids=("수집 역할", "마이그레이션 역할", "관리 역할", "기본 슈퍼유저", "루트 역할"),
)
def test_privileged_runtime_dsn_roles_are_rejected(tmp_path: Path, role: str) -> None:
    """수집·마이그레이션·관리자 성격 역할은 runtime DSN으로 사용할 수 없어야 한다."""

    env_file = _write_env(
        tmp_path / ".env",
        _valid_env(f"JEJU_RUNTIME_DSN=postgresql://{role}:password@localhost/jeju_trip"),
    )
    with pytest.raises(
        CodexMcpEnvironmentError, match="MCP_RUNTIME_DSN_PRIVILEGED_ROLE_FORBIDDEN"
    ):
        load_runtime_environment(env_file)
