"""로컬 운영 데이터와 live 통합 테스트 저장소의 격리 계약 테스트."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_live_test_services_use_dedicated_ephemeral_storage() -> None:
    """PostGIS와 MinIO live 테스트는 개발 서비스와 다른 포트·임시 저장소를 써야 한다."""

    compose = (ROOT / "infra/compose.local.yml").read_text()

    assert "postgres-test:" in compose
    assert "POSTGRES_DB: jeju_trip_test" in compose
    assert '"127.0.0.1:55433:5432"' in compose
    assert "minio-test:" in compose
    assert '"127.0.0.1:59010:9000"' in compose
    assert compose.count("tmpfs:") >= 2
