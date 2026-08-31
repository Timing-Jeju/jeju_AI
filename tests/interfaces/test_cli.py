"""수집 운영 CLI의 안전한 readiness 출력 테스트."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from botocore.exceptions import ClientError

from jeju_trip.infrastructure.projection_publisher import (
    SourceCoverageRecord,
    TimetableBundle,
)
from jeju_trip.infrastructure.raw_store import PrivateRawObjectStore
from jeju_trip.interfaces.cli import _ensure_versioned_raw_bucket, main

ROOT = Path(__file__).resolve().parents[2]


def test_cli_preflight_reports_missing_secret(monkeypatch, capsys) -> None:
    """승인 source에 API key가 없으면 CLI가 실패 코드와 검증 근거를 출력해야 한다."""

    monkeypatch.delenv("JEJU_TOURAPI_SERVICE_KEY", raising=False)
    exit_code = main(["preflight", "--source", "tourapi.place"])
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "상태: Fail" in output
    assert "SOURCE_SECRET_MISSING" in output


def test_cli_preflight_does_not_print_secret_value(monkeypatch, capsys) -> None:
    """CLI preflight 성공 출력에는 API key 값이 포함되지 않아야 한다."""

    monkeypatch.setenv("JEJU_TAGO_SERVICE_KEY", "never-print-this-secret")
    exit_code = main(["preflight", "--source", "tago.bus-stop"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "상태: Pass" in output
    assert "JEJU_TAGO_SERVICE_KEY" in output
    assert "never-print-this-secret" not in output


def test_coverage_cli_validates_csv_without_writing_by_default(tmp_path: Path, capsys) -> None:
    """coverage 명령은 execute가 없으면 CSV를 검증하되 DB를 변경하지 않아야 한다."""

    path = tmp_path / "coverage.csv"
    path.write_text(
        "capability,coverage_ratio,region_code,grid_id,service_date_from,"
        "service_date_to,blocking_reason\n"
        "place_search_ready,1,JEJU_ALL,ALL,2026-01-01,2026-12-31,\n",
        encoding="utf-8",
    )
    exit_code = main(
        [
            "publish-coverage",
            "--publication",
            str(uuid4()),
            "--file",
            str(path),
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "상태: Pass" in output
    assert "DB를 변경하지 않았습니다" in output


def test_entrance_capabilities_cannot_be_published_from_handwritten_ratio(
    tmp_path: Path, capsys
) -> None:
    """입구·접근성 readiness는 수기 비율이 아니라 projection 재측정으로만 발행해야 한다."""

    path = tmp_path / "coverage.csv"
    path.write_text(
        "capability,coverage_ratio,region_code,grid_id,service_date_from,"
        "service_date_to,blocking_reason\n"
        "accessibility_ready,1,JEJU_ALL,ALL,2026-08-31,2026-08-31,\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "publish-coverage",
            "--publication",
            str(uuid4()),
            "--file",
            str(path),
        ]
    )

    assert exit_code == 2
    assert "COVERAGE_MEASUREMENT_REQUIRED:accessibility_ready" in capsys.readouterr().out


def test_coverage_cli_can_append_date_to_active_publication(monkeypatch, capsys) -> None:
    """active publication 날짜 확장은 재활성화 없이 검증 coverage만 append해야 한다."""

    calls: list[str] = []

    class Measurer:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def measure(self, *args, active: bool = False, **kwargs) -> SourceCoverageRecord:
            assert active is True
            return SourceCoverageRecord(
                capability="fare_policy_ready",
                coverage_ratio=1,
                region_code="JEJU_EAST",
                grid_id="POC_V1",
            )

    class Publisher:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def append_active_coverage(self, *args, **kwargs) -> None:
            calls.append("append")

    monkeypatch.setenv("JEJU_IMPORTER_DSN", "postgresql://unused")
    monkeypatch.setattr("jeju_trip.interfaces.cli.PostgresCoverageMeasurer", Measurer)
    monkeypatch.setattr("jeju_trip.interfaces.cli.PostgresProjectionPublisher", Publisher)

    exit_code = main(
        [
            "measure-coverage",
            "--publication",
            str(uuid4()),
            "--capability",
            "fare_policy_ready",
            "--service-date-from",
            "2026-08-14",
            "--service-date-to",
            "2026-08-14",
            "--extend-active",
            "--execute",
        ]
    )

    assert exit_code == 0
    assert calls == ["append"]
    assert "날짜 coverage를 append" in capsys.readouterr().out


def test_aggregate_refresh_rejects_single_partial_query(capsys) -> None:
    """부분 조회 source는 단건 refresh로 active snapshot을 교체하지 않아야 한다."""

    exit_code = main(
        [
            "refresh",
            "--profile",
            "holiday-special-days",
            "--param",
            "solYear=2026",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "AGGREGATE_QUERY_FILE_REQUIRED" in output


def test_aggregate_refresh_accepts_valid_query_bundle(tmp_path: Path, monkeypatch, capsys) -> None:
    """연도 묶음 파일은 값 노출 없이 하나의 완전 snapshot 조회계획으로 검증돼야 한다."""

    query_file = tmp_path / "years.csv"
    query_file.write_text("solYear\n2026\n2027\n", encoding="utf-8")
    monkeypatch.setenv("JEJU_HOLIDAY_SERVICE_KEY", "secret-not-printed")
    exit_code = main(
        [
            "refresh",
            "--profile",
            "holiday-special-days",
            "--query-file",
            str(query_file),
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "secret-not-printed" not in output


def test_refresh_reports_staged_publication_as_success(tmp_path: Path, monkeypatch, capsys) -> None:
    """coverage 전 STAGED publication은 활성화 성공과 구분하되 CLI 실패로 처리하지 않아야 한다."""

    query_file = tmp_path / "routes.csv"
    query_file.write_text("routeId\nJEB405320111\n", encoding="utf-8")
    publication_id = uuid4()
    monkeypatch.setenv("JEJU_TAGO_SERVICE_KEY", "secret-not-printed")
    monkeypatch.setattr(
        "jeju_trip.interfaces.cli._execute_refresh",
        lambda *args: SimpleNamespace(
            status="STAGED",
            reason_code=None,
            dataset_version="staged-v1",
            publication_id=str(publication_id),
        ),
    )

    exit_code = main(
        [
            "refresh",
            "--profile",
            "tago-east-bus-route-stops",
            "--query-file",
            str(query_file),
            "--execute",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "상태: Pass" in output
    assert "status=STAGED" in output
    assert f"publication UUID: {publication_id}" in output


def test_aggregate_refresh_passes_the_reviewed_scope_expansion_baseline(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """전역 aggregate refresh는 검토한 활성 행 수를 실행 서비스까지 손실 없이 전달해야 한다."""

    query_file = tmp_path / "routes.csv"
    query_file.write_text("routeId\nJEB405320111\n", encoding="utf-8")
    received: list[int | None] = []

    def execute(*args, scope_expansion_from_rows=None):
        received.append(scope_expansion_from_rows)
        return SimpleNamespace(status="STAGED", reason_code=None, dataset_version="staged-v1")

    monkeypatch.setenv("JEJU_TAGO_SERVICE_KEY", "secret-not-printed")
    monkeypatch.setattr("jeju_trip.interfaces.cli._execute_refresh", execute)

    exit_code = main(
        [
            "refresh",
            "--profile",
            "tago-jeju-bus-route-stops",
            "--query-file",
            str(query_file),
            "--scope-expansion-from-rows",
            "4239",
            "--execute",
        ]
    )

    assert exit_code == 0
    assert received == [4239]
    assert "secret-not-printed" not in capsys.readouterr().out


def test_official_boundary_zip_can_be_validated_without_storage(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """공식 제주 경계 ZIP은 dry-run에서 단일 MultiPolygon만 확인하고 저장하지 않아야 한다."""

    path = tmp_path / "boundary.zip"
    path.write_bytes(b"official-zip")
    monkeypatch.setattr(
        "jeju_trip.interfaces.cli.normalize_sgis_jeju_boundary_zip",
        lambda raw, source_date: type("Batch", (), {"records": (object(),), "rejections": ()})(),
    )
    exit_code = main(
        [
            "manual-import",
            "--dataset",
            "jeju-boundary",
            "--file",
            str(path),
            "--source-date",
            "2025-06-30",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "records=1; geometry=MultiPolygon" in output
    assert "DB를 변경하지 않았습니다" in output


def test_manual_scope_expansion_baseline_is_limited_to_stop_identities(capsys) -> None:
    """수동 scope 확대 기준은 정류장 identity가 아닌 dataset에서 즉시 거부해야 한다."""

    exit_code = main(
        [
            "manual-import",
            "--dataset",
            "jeju-boundary",
            "--file",
            "unused.zip",
            "--source-date",
            "2026-08-18",
            "--scope-expansion-from-rows",
            "611",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "MANUAL_SCOPE_EXPANSION_BASELINE_INVALID" in output


def test_official_weekday_timetable_can_be_validated_without_storage(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """공식 평일 시간표 ZIP은 dry-run에서 raw 저장이나 DB 연결 없이 bundle을 검증해야 한다."""

    path = tmp_path / "official-timetable.zip"
    path.write_bytes(b"official-timetable")
    bundle = TimetableBundle((), (), (), ())
    monkeypatch.setattr(
        "jeju_trip.interfaces.cli.load_official_timetable_zip", lambda stream: object()
    )
    monkeypatch.setattr(
        "jeju_trip.interfaces.cli.build_weekday_timetable_bundle",
        lambda raw, target_date: bundle,
    )
    monkeypatch.setattr("jeju_trip.interfaces.cli.validate_timetable_bundle", lambda value: None)

    exit_code = main(
        [
            "manual-import",
            "--dataset",
            "official-bus-timetable",
            "--file",
            str(path),
            "--source-date",
            "2026-08-14",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "상태: Pass" in output
    assert "DB를 변경하지 않았습니다" in output


def test_fare_policy_can_be_validated_before_publication(capsys) -> None:
    """공식 요금 TOML은 DB publication 전에 동일 Pydantic 정책 계약으로 검증돼야 한다."""

    exit_code = main(
        [
            "manual-import",
            "--dataset",
            "bus-fare-policy",
            "--file",
            str(ROOT / "config/policies/jeju_bus_fare_2026-08-10.toml"),
            "--source-date",
            "2026-08-10",
        ]
    )
    assert exit_code == 0
    assert "DB를 변경하지 않았습니다" in capsys.readouterr().out


def test_restaurant_dietary_csv_can_be_validated_before_publication(
    tmp_path: Path, capsys
) -> None:
    """식당 식이 CSV는 private raw publication 전에 동일 typed 계약으로 dry-run 검증돼야 한다."""

    path = tmp_path / "restaurant-dietary.csv"
    path.write_text(
        "dietary_fact_id,place_fact_id,menu_item_id,menu_item_name,"
        "verified_free_from_allergens,verified_excludes_foods,verification_method,"
        "verified_at,expires_at,source_reference\n"
        "menu-1,tourapi.place:1,item-1,검증 메뉴,땅콩|우유,돼지고기,OFFICIAL,"
        "2026-08-30T10:00:00+09:00,2026-09-30T10:00:00+09:00,"
        "https://official.example/menu/1\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "manual-import",
            "--dataset",
            "restaurant-dietary-facts",
            "--file",
            str(path),
            "--source-date",
            "2026-08-30",
        ]
    )

    assert exit_code == 0
    assert "DB를 변경하지 않았습니다" in capsys.readouterr().out


def test_raw_bucket_setup_enables_and_confirms_versioning() -> None:
    """수집 CLI는 새 private raw bucket에 버전 보존을 켠 뒤에만 계속해야 한다."""

    class S3:
        created = False
        enabled = False

        def head_bucket(self, **kwargs):
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchBucket", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadBucket",
            )

        def create_bucket(self, **kwargs):
            self.created = kwargs["Bucket"] == PrivateRawObjectStore.bucket

        def get_bucket_versioning(self, **kwargs):
            return {"Status": "Enabled"} if self.enabled else {}

        def put_bucket_versioning(self, **kwargs):
            self.enabled = kwargs["VersioningConfiguration"] == {"Status": "Enabled"}

    s3 = S3()
    _ensure_versioned_raw_bucket(s3)

    assert s3.created is True
    assert s3.enabled is True
