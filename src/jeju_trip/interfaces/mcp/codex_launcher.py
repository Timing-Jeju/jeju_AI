"""owner-only dotenv에서 세 값만 주입하는 Codex MCP 전용 런처."""

from __future__ import annotations

import errno
import os
import re
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qs, unquote, urlsplit

EX_CONFIG = 78
MAX_ENV_FILE_BYTES = 64 * 1024
RUNTIME_KEYS = (
    "JEJU_RUNTIME_DSN",
    "JEJU_TMAP_API_KEY",
    "JEJU_TAGO_SERVICE_KEY",
)
SYSTEM_KEYS = (
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LIBPQ_USER_PATTERN = re.compile(
    r"(?:^|\s)user\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s]+))",
    re.IGNORECASE,
)
_PRIVILEGED_ROLE_PARTS = frozenset(
    {
        "admin",
        "administrator",
        "importer",
        "migrator",
        "owner",
        "postgres",
        "root",
        "superuser",
    }
)


class CodexMcpEnvironmentError(ValueError):
    """비밀값 없이 안정적인 코드만 노출하는 런처 설정 오류."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _raise(code: str) -> NoReturn:
    raise CodexMcpEnvironmentError(code)


def _validate_file_stat(file_stat: os.stat_result) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        _raise("MCP_ENV_FILE_NOT_REGULAR")
    if file_stat.st_uid != os.getuid():
        _raise("MCP_ENV_FILE_WRONG_OWNER")
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        _raise("MCP_ENV_FILE_PERMISSIONS_TOO_OPEN")
    if file_stat.st_size > MAX_ENV_FILE_BYTES:
        _raise("MCP_ENV_FILE_TOO_LARGE")


def _read_secure_environment_file(path: Path) -> str:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        _raise("MCP_ENV_FILE_MISSING")

    try:
        before_open = resolved.stat(follow_symlinks=False)
    except FileNotFoundError:
        _raise("MCP_ENV_FILE_MISSING")
    except OSError:
        _raise("MCP_ENV_FILE_NOT_REGULAR")
    _validate_file_stat(before_open)

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except FileNotFoundError:
        _raise("MCP_ENV_FILE_MISSING")
    except OSError as error:
        if error.errno == errno.ENOENT:
            _raise("MCP_ENV_FILE_MISSING")
        _raise("MCP_ENV_FILE_NOT_REGULAR")

    try:
        after_open = os.fstat(descriptor)
        _validate_file_stat(after_open)
        if (before_open.st_dev, before_open.st_ino) != (after_open.st_dev, after_open.st_ino):
            _raise("MCP_ENV_FILE_NOT_REGULAR")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8192, MAX_ENV_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_ENV_FILE_BYTES:
                _raise("MCP_ENV_FILE_TOO_LARGE")
    finally:
        os.close(descriptor)

    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        _raise("MCP_ENV_FILE_INVALID_UTF8")


def _decode_double_quoted(value: str) -> str:
    result: list[str] = []
    index = 1
    while index < len(value) - 1:
        character = value[index]
        if character != "\\":
            if character == '"':
                _raise("MCP_ENV_SYNTAX_INVALID")
            result.append(character)
            index += 1
            continue
        index += 1
        if index >= len(value) - 1:
            _raise("MCP_ENV_SYNTAX_INVALID")
        escaped = value[index]
        replacements = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
        if escaped not in replacements:
            _raise("MCP_ENV_SYNTAX_INVALID")
        result.append(replacements[escaped])
        index += 1
    return "".join(result)


def _parse_allowed_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if "$" in value or "`" in value:
        _raise("MCP_ENV_SYNTAX_INVALID")
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'") or "'" in value[1:-1]:
            _raise("MCP_ENV_SYNTAX_INVALID")
        return value[1:-1]
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            _raise("MCP_ENV_SYNTAX_INVALID")
        return _decode_double_quoted(value)
    if any(character.isspace() or character in "'\"" for character in value):
        _raise("MCP_ENV_SYNTAX_INVALID")
    return value


def _parse_runtime_values(content: str) -> dict[str, str]:
    selected: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        declaration = stripped
        if declaration.startswith("export"):
            if len(declaration) == len("export") or not declaration[len("export")].isspace():
                _raise("MCP_ENV_SYNTAX_INVALID")
            declaration = declaration[len("export") :].lstrip()
        if "=" not in declaration:
            _raise("MCP_ENV_SYNTAX_INVALID")
        key_text, raw_value = declaration.split("=", 1)
        key = key_text.strip()
        if not _KEY_PATTERN.fullmatch(key):
            _raise("MCP_ENV_SYNTAX_INVALID")
        if key in RUNTIME_KEYS:
            selected[key] = _parse_allowed_value(raw_value)
    return selected


def _dsn_roles(dsn: str) -> tuple[str, ...]:
    roles: list[str] = []
    try:
        parsed = urlsplit(dsn)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.scheme:
        if parsed.username:
            roles.append(unquote(parsed.username))
        roles.extend(parse_qs(parsed.query).get("user", ()))
    for match in _LIBPQ_USER_PATTERN.finditer(dsn):
        role = next((value for value in match.groups() if value is not None), "")
        roles.append(role)
    return tuple(roles)


def _validate_runtime_role(dsn: str) -> None:
    for role in _dsn_roles(dsn):
        parts = set(re.split(r"[._-]+", role.casefold()))
        if parts & _PRIVILEGED_ROLE_PARTS:
            _raise("MCP_RUNTIME_DSN_PRIVILEGED_ROLE_FORBIDDEN")


def load_runtime_environment(
    env_file: Path,
    *,
    parent_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """검증한 파일에서 세 값만 읽어 최소 MCP 프로세스 환경을 만든다."""

    parent = os.environ if parent_environment is None else parent_environment
    content = _read_secure_environment_file(env_file)
    selected = _parse_runtime_values(content)
    if any(not selected.get(key, "").strip() for key in RUNTIME_KEYS):
        _raise("MCP_ENV_REQUIRED_KEY_MISSING")
    _validate_runtime_role(selected["JEJU_RUNTIME_DSN"])
    runtime = {key: parent[key] for key in SYSTEM_KEYS if parent.get(key)}
    runtime.update({key: selected[key] for key in RUNTIME_KEYS})
    return runtime


def activate_runtime_environment(runtime_environment: Mapping[str, str]) -> None:
    """현재 프로세스 환경을 검증된 최소 allowlist로 원자적으로 교체한다."""

    os.environ.clear()
    os.environ.update(runtime_environment)


def _default_environment_file() -> Path:
    return Path(__file__).resolve().parents[4] / ".env"


def main() -> int:
    """env 파일 검증과 환경 격리 뒤 stdio MCP를 시작한다."""

    requested_file = os.environ.get("JEJU_MCP_ENV_FILE")
    env_file = Path(requested_file) if requested_file else _default_environment_file()
    try:
        runtime_environment = load_runtime_environment(env_file)
    except CodexMcpEnvironmentError as error:
        print(error.code, file=sys.stderr)
        return EX_CONFIG
    activate_runtime_environment(runtime_environment)

    from jeju_trip.interfaces.mcp.server import main as server_main

    server_main()
    return 0
