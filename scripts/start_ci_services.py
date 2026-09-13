"""이미지 다운로드만 제한적으로 재시도하고 격리 서비스의 준비 상태를 기다린다."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker", "compose", "--parallel", "1", "-f", "infra/compose.local.yml", "--profile", "test",
]
SERVICES = ["postgres-test", "minio-test"]


def _execute(arguments: list[str]) -> bool:
    try:
        return subprocess.run(arguments, cwd=ROOT, timeout=120, check=False).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def start_services() -> int:
    """세 번 이내 pull 성공 뒤에만 건강 상태를 확인하며 실패를 상위 CI에 전달한다."""
    for attempt in range(1, 4):
        if _execute([*COMPOSE, "pull", *SERVICES]):
            break
        print(f"CI_IMAGE_PULL_FAILED attempt={attempt}/3", flush=True)
        if attempt == 3:
            return 1
        time.sleep(attempt * 5)
    return 0 if _execute([
        *COMPOSE, "up", "-d", "--pull", "never", "--wait", "--wait-timeout", "90", *SERVICES,
    ]) else 1


if __name__ == "__main__":
    raise SystemExit(start_services())
