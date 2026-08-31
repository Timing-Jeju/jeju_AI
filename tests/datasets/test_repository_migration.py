"""Timing-Jeju 대상 저장소 이관 경계 테스트."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_quality_workflow_runs_after_develop_push() -> None:
    """대상 기본 브랜치 develop에 병합된 뒤에도 전체 품질 게이트가 실행돼야 한다."""

    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )

    assert "branches: [main, develop]" in workflow


def test_codex_setup_does_not_pin_the_previous_checkout_path() -> None:
    """이관된 등록 안내는 이전 jeju_algo 절대 경로를 Codex 설정에 고정하지 않아야 한다."""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "/Users/gwongwangjae/jeju_algo" not in readme
    assert "REPOSITORY_DIR=/absolute/path/to/jeju_AI" in readme
