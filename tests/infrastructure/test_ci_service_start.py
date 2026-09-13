"""CI 서비스 준비의 제한된 재시도 검증."""

import subprocess
from unittest.mock import patch

from scripts.start_ci_services import start_services


def test_pull_transient_failure_retries_then_waits_for_health() -> None:
    """이미지 다운로드의 일시 실패만 재시도하고 건강 상태를 확인해야 한다."""
    with patch("scripts.start_ci_services.subprocess.run") as run, patch(
        "scripts.start_ci_services.time.sleep"
    ) as sleep:
        run.side_effect = [
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0),
        ]
        assert start_services() == 0
        assert run.call_count == 3
        assert "pull" in run.call_args_list[0].args[0]
        assert "pull" in run.call_args_list[1].args[0]
        assert "--wait" in run.call_args_list[2].args[0]
        assert "never" in run.call_args_list[2].args[0]
        sleep.assert_called_once_with(5)


def test_pull_failure_exhaustion_does_not_start_services() -> None:
    """세 번의 다운로드 실패 후 서비스 기동 없이 실패를 반환해야 한다."""
    with patch("scripts.start_ci_services.subprocess.run") as run, patch(
        "scripts.start_ci_services.time.sleep"
    ) as sleep:
        run.return_value = subprocess.CompletedProcess([], 1)
        assert start_services() == 1
        assert run.call_count == 3
        assert all("pull" in call.args[0] for call in run.call_args_list)
        assert [call.args[0] for call in sleep.call_args_list] == [5, 10]


def test_health_failure_is_not_retried_or_hidden() -> None:
    """서비스 건강 검사 실패는 재기동으로 숨기지 않고 실패해야 한다."""
    with patch("scripts.start_ci_services.subprocess.run") as run:
        run.side_effect = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)]
        assert start_services() == 1
        assert run.call_count == 2


def test_pull_timeout_is_bounded_and_retryable() -> None:
    """다운로드가 멈춰도 명령 제한시간과 최대 시도 횟수를 지켜야 한다."""
    with patch("scripts.start_ci_services.subprocess.run") as run, patch(
        "scripts.start_ci_services.time.sleep"
    ):
        run.side_effect = subprocess.TimeoutExpired("docker", 120)
        assert start_services() == 1
        assert run.call_count == 3
        assert all(call.kwargs["timeout"] == 120 for call in run.call_args_list)
