"""수집 준비상태를 한국어 우선 형식으로 출력하는 CLI."""

from __future__ import annotations

import argparse
import csv
import io
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
import httpx
import psycopg
from botocore.exceptions import ClientError

from jeju_trip.application.manual_import import (
    OPENING_HOURS_FILES,
    SCOPE_MANIFEST_FILES,
    TIMETABLE_FILES,
    ManualCsvImportService,
    validate_scope_manifest_bundle,
    validate_timetable_bundle,
)
from jeju_trip.application.refresh_service import PublicDataRefreshService
from jeju_trip.infrastructure.activation_repository import PostgresActivationRepository
from jeju_trip.infrastructure.bus_stop_publisher import PostgresBusStopPublisher
from jeju_trip.infrastructure.coverage_measurement import PostgresCoverageMeasurer
from jeju_trip.infrastructure.official_timetable_bundle import build_weekday_timetable_bundle
from jeju_trip.infrastructure.preflight import SourcePreflight
from jeju_trip.infrastructure.projection_publisher import (
    OpeningHoursBundle,
    PostgresProjectionPublisher,
    ScopeManifestBundle,
    SourceCoverageRecord,
    TimetableBundle,
)
from jeju_trip.infrastructure.public_data_http import BoundedPublicDataClient
from jeju_trip.infrastructure.public_data_normalizers import (
    PlaceOpeningObservationRecord,
    PlaceOpeningRuleRecord,
    PlaceWeeklyClosureRecord,
    normalize_bus_route_stops,
    normalize_holidays,
    normalize_itinerary_template_candidates,
    normalize_itinerary_template_steps,
    normalize_place_entrances,
    normalize_place_opening_rules,
    normalize_place_schedule_exceptions,
    normalize_restaurant_dietary_facts,
    normalize_route_service_exceptions,
    normalize_scheduled_stop_times,
    normalize_scheduled_trips,
    normalize_scope_members,
    normalize_service_calendar_exceptions,
    normalize_service_calendars,
    normalize_stop_identities,
    normalize_tago_jeju_stops,
    normalize_tago_route_stops,
    normalize_tago_routes,
    normalize_tago_stops,
    normalize_timetable_notices,
    normalize_tour_intro_opening_snapshot,
    normalize_tour_places,
)
from jeju_trip.infrastructure.raw_store import PrivateRawObjectStore
from jeju_trip.infrastructure.refresh_profiles import RefreshProfileCatalog
from jeju_trip.infrastructure.s3_object_client import S3ObjectClient
from jeju_trip.infrastructure.sgis_boundary import normalize_sgis_jeju_boundary_zip
from jeju_trip.infrastructure.source_admin_repository import PostgresSourceAdminRepository
from jeju_trip.infrastructure.source_catalog import SourceCatalog
from jeju_trip.infrastructure.timetable_xlsx import load_official_timetable_zip
from jeju_trip.planning.policy import (
    BusFarePolicy,
    TaxiFarePolicy,
    load_bus_fare_policy,
    load_taxi_fare_policy,
)

ROOT = Path(__file__).resolve().parents[3]


def _print_status(
    status: str,
    source_id: str,
    failure_code: str = "",
    evidence: str = "",
    risk: str = "",
    publication_id: str = "",
) -> None:
    print(f"상태: {status}")
    print(f"소스 ID: {source_id}")
    print("데이터 기준:")
    print("페이지 수:")
    print("원본 행 수:")
    print("정상 행 수:")
    print("거부 행 수:")
    print("좌표 충족률:")
    print("데이터셋 버전:")
    print(f"publication UUID: {publication_id}")
    print(f"실패 코드: {failure_code}")
    print(f"검증 근거: {evidence}")
    print(f"잔여 위험: {risk}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jeju-data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "inspect"):
        child = subparsers.add_parser(command)
        child.add_argument("--source", required=True)
    refresh = subparsers.add_parser("refresh")
    refresh.add_argument("--profile", required=True)
    refresh.add_argument("--param", action="append", default=[])
    refresh.add_argument("--query-file")
    refresh.add_argument("--resume-acquisition")
    refresh.add_argument("--scope-expansion-from-rows", type=int)
    refresh.add_argument("--execute", action="store_true")
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--source", required=True)
    rollback.add_argument("--publication", required=True)
    manual = subparsers.add_parser("manual-import")
    manual.add_argument(
        "--dataset",
        required=True,
        choices=(
            "place-entrances",
            "restaurant-dietary-facts",
            "place-opening-hours",
            "stop-identities",
            "bus-timetable",
            "official-bus-timetable",
            "jeju-boundary",
            "taxi-fare-policy",
            "bus-fare-policy",
            "service-scope-manifest",
        ),
    )
    manual.add_argument("--file", action="append", required=True)
    manual.add_argument("--source-date", required=True)
    manual.add_argument("--scope-expansion-from-rows", type=int)
    manual.add_argument("--execute", action="store_true")
    coverage = subparsers.add_parser("publish-coverage")
    coverage.add_argument("--publication", required=True)
    coverage.add_argument("--file", required=True)
    coverage.add_argument("--execute", action="store_true")
    measured_coverage = subparsers.add_parser("measure-coverage")
    measured_coverage.add_argument("--publication", required=True)
    measured_coverage.add_argument(
        "--capability",
        required=True,
        choices=(
            "service_area_ready",
            "place_search_ready",
            "opening_hours_ready",
            "opening_hours_snapshot_ready",
            "verified_entrances_ready",
            "accessibility_ready",
            "restaurant_recommendation_ready",
            "confirmed_stop_mapping_ready",
            "bus_fare_policy_ready",
            "taxi_fare_policy_ready",
            "fare_policy_ready",
            "future_bus_planning_ready",
            "bus_route_catalog_ready",
            "bus_route_stop_catalog_ready",
        ),
    )
    measured_coverage.add_argument("--service-date-from")
    measured_coverage.add_argument("--service-date-to")
    measured_coverage.add_argument("--region-code", default="JEJU_EAST")
    measured_coverage.add_argument("--grid-id", default="POC_V1")
    measured_coverage.add_argument("--extend-active", action="store_true")
    measured_coverage.add_argument("--execute", action="store_true")
    return parser


def _parameters(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in result:
            raise ValueError("REFRESH_PARAMETER_INVALID")
        result[key] = item
    return result


def _refresh_queries(
    profile, values: list[str], query_file: str | None
) -> tuple[dict[str, str], ...]:
    if profile.snapshot_mode == "complete":
        if query_file:
            raise ValueError("COMPLETE_SNAPSHOT_QUERY_FILE_FORBIDDEN")
        return (profile.materialize_query(_parameters(values)),)
    if values or not query_file:
        raise ValueError("AGGREGATE_QUERY_FILE_REQUIRED")
    with Path(query_file).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != profile.required_parameters:
            raise ValueError("AGGREGATE_QUERY_HEADER_INVALID")
        queries = tuple(
            profile.materialize_query(
                {name: str(row.get(name) or "").strip() for name in profile.required_parameters}
            )
            for row in reader
        )
    if not queries:
        raise ValueError("AGGREGATE_QUERY_FILE_EMPTY")
    if len({tuple(sorted(query.items())) for query in queries}) != len(queries):
        raise ValueError("AGGREGATE_QUERY_DUPLICATED")
    return queries


def _required_environment(names: tuple[str, ...]) -> dict[str, str]:
    missing = tuple(name for name in names if not os.getenv(name))
    if missing:
        raise ValueError(f"IMPORTER_ENV_MISSING:{','.join(missing)}")
    return {name: os.environ[name] for name in names}


def _ensure_versioned_raw_bucket(s3) -> None:
    """원본 bucket을 준비하고 버전 보존을 활성화한다."""

    try:
        s3.head_bucket(Bucket=PrivateRawObjectStore.bucket)
    except ClientError as error:
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise
        s3.create_bucket(Bucket=PrivateRawObjectStore.bucket)
    versioning = s3.get_bucket_versioning(Bucket=PrivateRawObjectStore.bucket)
    if versioning.get("Status") != "Enabled":
        s3.put_bucket_versioning(
            Bucket=PrivateRawObjectStore.bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
    confirmed = s3.get_bucket_versioning(Bucket=PrivateRawObjectStore.bucket)
    if confirmed.get("Status") != "Enabled":
        raise ValueError("RAW_BUCKET_VERSIONING_REQUIRED")


def _execute_refresh(
    source,
    profile,
    queries,
    environment,
    *,
    scope_expansion_from_rows: int | None = None,
    resume_acquisition_id: UUID | None = None,
):
    runtime = _required_environment(
        (
            "JEJU_IMPORTER_DSN",
            "JEJU_RAW_S3_ENDPOINT",
            "JEJU_RAW_S3_ACCESS_KEY",
            "JEJU_RAW_S3_SECRET_KEY",
        )
    )
    s3 = boto3.client(
        "s3",
        endpoint_url=runtime["JEJU_RAW_S3_ENDPOINT"],
        aws_access_key_id=runtime["JEJU_RAW_S3_ACCESS_KEY"],
        aws_secret_access_key=runtime["JEJU_RAW_S3_SECRET_KEY"],
        region_name=os.getenv("JEJU_RAW_S3_REGION", "ap-northeast-2"),
    )
    _ensure_versioned_raw_bucket(s3)
    dsn = runtime["JEJU_IMPORTER_DSN"]
    resume_arguments: dict[str, Any] = {}
    if resume_acquisition_id is not None:
        with psycopg.connect(dsn) as connection:
            row = connection.execute(
                """SELECT raw.bucket, raw.object_key, raw.object_version_id,
                          acquisition.observed_at
                     FROM source_admin.acquisition acquisition
                     JOIN source_admin.raw_object raw
                       ON raw.source_id = acquisition.source_id
                      AND raw.checksum = acquisition.raw_checksum
                    WHERE acquisition.acquisition_id = %s
                      AND acquisition.source_id = %s
                      AND (
                        acquisition.status = 'INCOMPLETE'
                        OR (
                          acquisition.normalization_schema_version <> %s
                          AND acquisition.status IN ('STAGED', 'PUBLISHED', 'NO_CHANGE')
                        )
                      )""",
                (
                    resume_acquisition_id,
                    source.id,
                    source.normalization_schema_version,
                ),
            ).fetchone()
        if row is None or not isinstance(row[3], datetime):
            raise ValueError("REFRESH_RESUME_ACQUISITION_INVALID")
        get_arguments = {"Bucket": str(row[0]), "Key": str(row[1])}
        if str(row[2] or "").strip():
            get_arguments["VersionId"] = str(row[2])
        resume_arguments = {
            "resume_raw_archive": s3.get_object(**get_arguments)["Body"].read(),
            "resume_observed_at": row[3],
        }
    source_admin = PostgresSourceAdminRepository(dsn, assume_role="jeju_importer")
    projections = PostgresProjectionPublisher(dsn, assume_role="jeju_importer")
    normalizers = {
        "tour_places": normalize_tour_places,
        "tago_routes": normalize_tago_routes,
        "tago_route_stops": normalize_tago_route_stops,
        "holidays": normalize_holidays,
        "tago_stops": normalize_tago_stops,
        "tago_jeju_stops": normalize_tago_jeju_stops,
        "tour_intro_opening_rules": lambda rows: normalize_tour_intro_opening_snapshot(
            rows, queries
        ),
    }
    publishers = {
        "places": projections.publish_places,
        "routes": projections.publish_routes,
        "route_stops": projections.publish_route_stops,
        "holidays": projections.publish_holidays,
        "bus_stops": PostgresBusStopPublisher(dsn, assume_role="jeju_importer").publish,
        "opening_hours": lambda acquisition_id, records: projections.publish_opening_hours(
            acquisition_id,
            OpeningHoursBundle(
                tuple(item for item in records if isinstance(item, PlaceOpeningRuleRecord)),
                (),
                tuple(item for item in records if isinstance(item, PlaceWeeklyClosureRecord)),
                tuple(
                    item
                    for item in records
                    if isinstance(item, PlaceOpeningObservationRecord)
                ),
            ),
        ),
    }
    with httpx.Client(timeout=15) as client:
        service = PublicDataRefreshService(
            BoundedPublicDataClient(client),
            PrivateRawObjectStore(S3ObjectClient(s3)),
            source_admin,
        )
        return service.refresh_batch(
            source,
            profile.endpoint,
            queries,
            environment,
            normalizers[profile.normalizer],
            rows_per_page=profile.rows_per_page,
            publish_validated=publishers[profile.publisher],
            scope_expansion_from_rows=scope_expansion_from_rows,
            stop_on_incomplete=profile.snapshot_mode == "aggregate",
            **resume_arguments,
        )


def _manual_files(dataset: str, values: list[str]) -> dict[str, Path]:
    bundle_names = (
        set(TIMETABLE_FILES)
        if dataset == "bus-timetable"
        else set(OPENING_HOURS_FILES)
        if dataset == "place-opening-hours"
        else set(SCOPE_MANIFEST_FILES)
        if dataset == "service-scope-manifest"
        else None
    )
    if bundle_names is None:
        if len(values) != 1 or "=" in values[0]:
            raise ValueError("MANUAL_SINGLE_FILE_INVALID")
        return {"records.csv": Path(values[0])}
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or name not in bundle_names or name in result:
            raise ValueError("MANUAL_BUNDLE_FILE_ARGUMENT_INVALID")
        result[name] = Path(raw_path)
    if set(result) != bundle_names:
        raise ValueError("MANUAL_BUNDLE_FILES_INVALID")
    return result


def _manual_dependencies():
    runtime = _required_environment(
        (
            "JEJU_IMPORTER_DSN",
            "JEJU_RAW_S3_ENDPOINT",
            "JEJU_RAW_S3_ACCESS_KEY",
            "JEJU_RAW_S3_SECRET_KEY",
        )
    )
    s3 = boto3.client(
        "s3",
        endpoint_url=runtime["JEJU_RAW_S3_ENDPOINT"],
        aws_access_key_id=runtime["JEJU_RAW_S3_ACCESS_KEY"],
        aws_secret_access_key=runtime["JEJU_RAW_S3_SECRET_KEY"],
        region_name=os.getenv("JEJU_RAW_S3_REGION", "ap-northeast-2"),
    )
    _ensure_versioned_raw_bucket(s3)
    dsn = runtime["JEJU_IMPORTER_DSN"]
    return (
        ManualCsvImportService(
            PrivateRawObjectStore(S3ObjectClient(s3)),
            PostgresSourceAdminRepository(dsn, assume_role="jeju_importer"),
        ),
        PostgresProjectionPublisher(dsn, assume_role="jeju_importer"),
    )


def _coverage_records(path: Path) -> tuple[SourceCoverageRecord, ...]:
    measured_only = {
        "verified_entrances_ready",
        "accessibility_ready",
        "restaurant_recommendation_ready",
    }
    allowed = {
        "service_area_ready",
        "place_search_ready",
        "opening_hours_ready",
        "opening_hours_snapshot_ready",
        "verified_entrances_ready",
        "confirmed_stop_mapping_ready",
        "bus_fare_policy_ready",
        "taxi_fare_policy_ready",
        "fare_policy_ready",
        "walking_routing_ready",
        "driving_routing_ready",
        "future_bus_planning_ready",
        "restaurant_recommendation_ready",
        "cafe_recommendation_ready",
        "realtime_bus_ready",
        "accessibility_ready",
        "scope_manifest_ready",
    }
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != [
            "capability",
            "coverage_ratio",
            "region_code",
            "grid_id",
            "service_date_from",
            "service_date_to",
            "blocking_reason",
        ]:
            raise ValueError("COVERAGE_CSV_HEADER_INVALID")
        records = []
        for row in reader:
            capability = str(row["capability"] or "").strip()
            if capability not in allowed:
                raise ValueError("CAPABILITY_UNKNOWN")
            if capability in measured_only:
                raise ValueError(f"COVERAGE_MEASUREMENT_REQUIRED:{capability}")
            records.append(
                SourceCoverageRecord(
                    capability=capability,
                    coverage_ratio=float(row["coverage_ratio"]),
                    region_code=str(row["region_code"] or "").strip(),
                    grid_id=str(row["grid_id"] or "").strip(),
                    service_date_from=(
                        date.fromisoformat(row["service_date_from"])
                        if row["service_date_from"]
                        else None
                    ),
                    service_date_to=(
                        date.fromisoformat(row["service_date_to"])
                        if row["service_date_to"]
                        else None
                    ),
                    blocking_reason=str(row["blocking_reason"] or "").strip() or None,
                )
            )
    if not records:
        raise ValueError("COVERAGE_RECORDS_EMPTY")
    return tuple(records)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    catalog = SourceCatalog.load(ROOT / "config" / "data_sources.toml")
    if arguments.command == "measure-coverage":
        try:
            runtime = _required_environment(("JEJU_IMPORTER_DSN",))
            publication_id = UUID(arguments.publication)
            record = PostgresCoverageMeasurer(
                runtime["JEJU_IMPORTER_DSN"], assume_role="jeju_importer"
            ).measure(
                publication_id,
                arguments.capability,
                date.fromisoformat(arguments.service_date_from)
                if arguments.service_date_from
                else None,
                date.fromisoformat(arguments.service_date_to)
                if arguments.service_date_to
                else None,
                arguments.region_code,
                arguments.grid_id,
                active=arguments.extend_active,
            )
            if arguments.execute:
                publisher = PostgresProjectionPublisher(
                    runtime["JEJU_IMPORTER_DSN"], assume_role="jeju_importer"
                )
                if arguments.extend_active:
                    publisher.append_active_coverage(publication_id, (record,))
                else:
                    publisher.publish_coverage(publication_id, (record,))
        except Exception as error:
            _print_status(
                "Fail",
                "source_coverage",
                str(error) if isinstance(error, ValueError) else type(error).__name__,
                "projection coverage measurement",
                "원본 행이나 좌표를 출력하지 않고 중단했습니다.",
            )
            return 2
        _print_status(
            "Pass",
            "source_coverage",
            evidence=f"capability={record.capability}; ratio={record.coverage_ratio:.6f}",
            risk=(
                (
                    "active publication에 날짜 coverage를 append했습니다."
                    if arguments.extend_active
                    else "측정 coverage를 발행했습니다."
                )
                if arguments.execute
                else "측정만 수행했고 DB에 coverage를 추가하지 않았습니다."
            ),
        )
        return 0
    if arguments.command == "publish-coverage":
        try:
            records = _coverage_records(Path(arguments.file))
            publication_id = UUID(arguments.publication)
            if arguments.execute:
                runtime = _required_environment(("JEJU_IMPORTER_DSN",))
                PostgresProjectionPublisher(
                    runtime["JEJU_IMPORTER_DSN"], assume_role="jeju_importer"
                ).publish_coverage(publication_id, records)
        except Exception as error:
            _print_status(
                "Fail",
                "source_coverage",
                str(error) if isinstance(error, ValueError) else type(error).__name__,
                "coverage validation",
                "파일 내용·경로를 출력하지 않고 중단했습니다.",
            )
            return 2
        _print_status(
            "Pass",
            "source_coverage",
            evidence=f"records={len(records)}",
            risk=(
                "coverage가 active publication에 append됐습니다."
                if arguments.execute
                else "--execute가 없어 DB를 변경하지 않았습니다."
            ),
        )
        return 0
    if arguments.command == "manual-import":
        source_ids = {
            "place-entrances": "travel.place-entrance-map",
            "restaurant-dietary-facts": "travel.restaurant-dietary-map",
            "place-opening-hours": "travel.place-hours-map",
            "stop-identities": "transport.stop-identity-map",
            "bus-timetable": "jeju.bus-timetable",
            "official-bus-timetable": "jeju.bus-timetable",
            "jeju-boundary": "spatial.jeju-boundary",
            "taxi-fare-policy": "jeju.taxi-fare-policy",
            "bus-fare-policy": "jeju.bus-fare-policy",
            "service-scope-manifest": "travel.service-scope-manifest",
        }
        source_id = source_ids[arguments.dataset]
        try:
            if arguments.scope_expansion_from_rows is not None and (
                arguments.dataset != "stop-identities" or arguments.scope_expansion_from_rows <= 0
            ):
                raise ValueError("MANUAL_SCOPE_EXPANSION_BASELINE_INVALID")
            source = (
                catalog.require(source_id)
                if arguments.execute
                else next(item for item in catalog.sources if item.id == source_id)
            )
            source_date = date.fromisoformat(arguments.source_date)
            files = _manual_files(arguments.dataset, arguments.file)
            if arguments.dataset == "jeju-boundary":
                raw = files["records.csv"].read_bytes()
                if len(raw) > source.acquisition.maximum_response_bytes:
                    raise ValueError("MANUAL_SOURCE_TOO_LARGE")
                boundary_preview = normalize_sgis_jeju_boundary_zip(raw, source_date=source_date)
                if boundary_preview.rejections:
                    raise ValueError(boundary_preview.rejections[0].reason_code)
                rows = {}
            elif arguments.dataset in {"taxi-fare-policy", "bus-fare-policy"}:
                if arguments.dataset == "taxi-fare-policy":
                    load_taxi_fare_policy(files["records.csv"])
                else:
                    load_bus_fare_policy(files["records.csv"])
                rows = {}
            elif arguments.dataset == "official-bus-timetable":
                raw = files["records.csv"].read_bytes()
                if len(raw) > source.acquisition.maximum_response_bytes:
                    raise ValueError("MANUAL_SOURCE_TOO_LARGE")
                official = load_official_timetable_zip(io.BytesIO(raw))
                validate_timetable_bundle(build_weekday_timetable_bundle(official, source_date))
                rows = {}
            else:
                rows, _ = ManualCsvImportService._read_and_archive(source, files)
            normalizers = {
                "place-entrances": normalize_place_entrances,
                "restaurant-dietary-facts": normalize_restaurant_dietary_facts,
                "stop-identities": normalize_stop_identities,
            }
            if arguments.dataset in {
                "jeju-boundary",
                "taxi-fare-policy",
                "bus-fare-policy",
                "official-bus-timetable",
            }:
                pass
            elif arguments.dataset in normalizers:
                preview = normalizers[arguments.dataset](rows["records.csv"])
                if preview.rejections:
                    raise ValueError("MANUAL_ROWS_REJECTED")
            elif arguments.dataset == "place-opening-hours":
                rules = normalize_place_opening_rules(rows["place-opening-rules.csv"])
                exceptions = normalize_place_schedule_exceptions(
                    rows["place-schedule-exceptions.csv"]
                )
                if rules.rejections or exceptions.rejections:
                    raise ValueError("MANUAL_ROWS_REJECTED")
            elif arguments.dataset == "service-scope-manifest":
                members = normalize_scope_members(rows["service-scope-members.csv"])
                steps = normalize_itinerary_template_steps(rows["itinerary-template-steps.csv"])
                candidates = normalize_itinerary_template_candidates(
                    rows["itinerary-template-candidates.csv"]
                )
                if members.rejections or steps.rejections or candidates.rejections:
                    raise ValueError("MANUAL_ROWS_REJECTED")
                validate_scope_manifest_bundle(
                    ScopeManifestBundle(members.records, steps.records, candidates.records)
                )
            else:
                route_stops = normalize_bus_route_stops(rows["bus-route-stops.csv"])
                calendars = normalize_service_calendars(rows["service-calendars.csv"])
                trips = normalize_scheduled_trips(rows["scheduled-trips.csv"])
                stop_times = normalize_scheduled_stop_times(rows["scheduled-stop-times.csv"])
                service_exceptions = normalize_service_calendar_exceptions(
                    rows["service-calendar-exceptions.csv"]
                )
                notices = normalize_timetable_notices(rows["timetable-notices.csv"])
                route_exceptions = normalize_route_service_exceptions(
                    rows["route-service-exceptions.csv"]
                )
                timetable_batches = (
                    route_stops,
                    calendars,
                    trips,
                    stop_times,
                    service_exceptions,
                    notices,
                    route_exceptions,
                )
                if any(batch.rejections for batch in timetable_batches):
                    raise ValueError("MANUAL_ROWS_REJECTED")
                validate_timetable_bundle(
                    TimetableBundle(
                        route_stops.records,
                        calendars.records,
                        trips.records,
                        stop_times.records,
                        service_exceptions.records,
                        notices.records,
                        route_exceptions.records,
                    )
                )
            if not arguments.execute:
                _print_status(
                    "Pass",
                    source_id,
                    evidence=(
                        "records=1; geometry=MultiPolygon; local validation complete"
                        if arguments.dataset == "jeju-boundary"
                        else f"files={len(files)}; local validation complete"
                    ),
                    risk="--execute가 없어 object storage와 DB를 변경하지 않았습니다.",
                )
                return 0
            service, publisher = _manual_dependencies()
            if arguments.dataset == "taxi-fare-policy":
                outcome = service.import_toml(
                    source,
                    files["records.csv"],
                    source_date,
                    TaxiFarePolicy.model_validate,
                    publisher.publish_taxi_fare_policy,
                )
            elif arguments.dataset == "bus-fare-policy":
                outcome = service.import_toml(
                    source,
                    files["records.csv"],
                    source_date,
                    BusFarePolicy.model_validate,
                    publisher.publish_bus_fare_policy,
                )
            elif arguments.dataset == "jeju-boundary":
                outcome = service.import_boundary_zip(
                    source,
                    files["records.csv"],
                    source_date,
                    publisher.publish_boundary,
                )
            elif arguments.dataset == "official-bus-timetable":
                outcome = service.import_official_timetable(
                    source,
                    files["records.csv"],
                    source_date,
                    publisher.publish_timetable,
                )
            elif arguments.dataset == "place-entrances":
                outcome = service.import_single(
                    source,
                    files["records.csv"],
                    source_date,
                    normalize_place_entrances,
                    publisher.publish_entrances,
                )
            elif arguments.dataset == "restaurant-dietary-facts":
                outcome = service.import_single(
                    source,
                    files["records.csv"],
                    source_date,
                    normalize_restaurant_dietary_facts,
                    publisher.publish_restaurant_dietary_facts,
                )
            elif arguments.dataset == "stop-identities":
                outcome = service.import_single(
                    source,
                    files["records.csv"],
                    source_date,
                    normalize_stop_identities,
                    publisher.publish_stop_identities,
                    scope_expansion_from_rows=arguments.scope_expansion_from_rows,
                )
            elif arguments.dataset == "place-opening-hours":
                outcome = service.import_opening_hours(
                    source, files, source_date, publisher.publish_opening_hours
                )
            elif arguments.dataset == "service-scope-manifest":
                outcome = service.import_scope_manifest(
                    source, files, source_date, publisher.publish_scope_manifest
                )
            else:
                outcome = service.import_timetable(
                    source, files, source_date, publisher.publish_timetable
                )
            if arguments.dataset == "jeju-boundary" and outcome.publication_id:
                publication_id = UUID(outcome.publication_id)
                dsn = os.environ["JEJU_IMPORTER_DSN"]
                coverage = PostgresCoverageMeasurer(dsn, assume_role="jeju_importer").measure(
                    publication_id, "service_area_ready"
                )
                publisher.publish_coverage(publication_id, (coverage,))
        except Exception as error:
            _print_status(
                "Fail",
                source_id,
                str(error) if isinstance(error, ValueError) else type(error).__name__,
                "manual import validation",
                "파일 내용·경로·좌표를 출력하지 않고 중단했습니다.",
            )
            return 2
        successful_statuses = {"STAGED", "PUBLISHED", "NO_CHANGE"}
        _print_status(
            "Pass" if outcome.status in successful_statuses else "Fail",
            source_id,
            outcome.reason_code or "",
            f"status={outcome.status}; dataset={outcome.dataset_version or ''}",
            (
                "service_area_ready coverage를 측정·발행했습니다."
                if arguments.dataset == "jeju-boundary" and outcome.publication_id
                else "coverage는 별도 측정·발행 전까지 활성화되지 않습니다."
            ),
            outcome.publication_id or "",
        )
        return 0 if outcome.status in successful_statuses else 2
    if arguments.command == "refresh":
        profiles = RefreshProfileCatalog.load(ROOT / "config" / "refresh_profiles.toml", catalog)
        try:
            profile = profiles.require(arguments.profile)
            queries = _refresh_queries(profile, arguments.param, arguments.query_file)
            if arguments.scope_expansion_from_rows is not None and (
                arguments.scope_expansion_from_rows <= 0 or profile.snapshot_mode != "aggregate"
            ):
                raise ValueError("SCOPE_EXPANSION_REQUIRES_AGGREGATE_PROFILE")
            resume_acquisition_id = (
                UUID(arguments.resume_acquisition) if arguments.resume_acquisition else None
            )
            if resume_acquisition_id is not None and profile.snapshot_mode != "aggregate":
                raise ValueError("REFRESH_RESUME_REQUIRES_AGGREGATE_PROFILE")
        except ValueError as error:
            _print_status("Fail", "", str(error), "refresh profile validation", "")
            return 2
        source = catalog.require(profile.source_id)
        readiness = SourcePreflight().check(source, dict(os.environ))
        if readiness.status != "PASS":
            _print_status(
                "Fail",
                source.id,
                ",".join(readiness.reason_codes),
                "; ".join(readiness.evidence),
                "preflight 실패로 네트워크를 호출하지 않았습니다.",
            )
            return 2
        if not arguments.execute:
            _print_status(
                "Pass",
                source.id,
                evidence=f"profile={profile.name}; contract={source.contract_fingerprint}",
                risk="--execute가 없어 네트워크와 저장소를 변경하지 않았습니다.",
            )
            return 0
        try:
            expansion_arguments: dict[str, Any] = (
                {"scope_expansion_from_rows": arguments.scope_expansion_from_rows}
                if arguments.scope_expansion_from_rows is not None
                else {}
            )
            resume_arguments: dict[str, Any] = (
                {"resume_acquisition_id": resume_acquisition_id}
                if resume_acquisition_id is not None
                else {}
            )
            outcome = _execute_refresh(
                source,
                profile,
                queries,
                dict(os.environ),
                **expansion_arguments,
                **resume_arguments,
            )
        except Exception as error:
            _print_status(
                "Fail",
                source.id,
                type(error).__name__,
                f"profile={profile.name}",
                "원문·secret·좌표를 출력하지 않고 refresh를 중단했습니다.",
            )
            return 2
        successful_statuses = {"STAGED", "PUBLISHED", "NO_CHANGE"}
        _print_status(
            "Pass" if outcome.status in successful_statuses else "Fail",
            source.id,
            outcome.reason_code or "",
            (
                f"status={outcome.status}; dataset={outcome.dataset_version or ''}; "
                f"queries={getattr(outcome, 'completed_query_count', 0)}/"
                f"{getattr(outcome, 'query_count', 0)}; "
                f"acquisition={getattr(outcome, 'acquisition_id', None) or ''}"
            ),
            "실제 capability 활성화는 source_coverage publication 판정을 따릅니다.",
            getattr(outcome, "publication_id", None) or "",
        )
        return 0 if outcome.status in successful_statuses else 2
    if arguments.command == "rollback":
        matches = [source for source in catalog.sources if source.id == arguments.source]
        if not matches:
            _print_status("Fail", arguments.source, "SOURCE_UNKNOWN", "catalog lookup", "")
            return 2
        try:
            runtime = _required_environment(("JEJU_IMPORTER_DSN",))
            PostgresActivationRepository(
                runtime["JEJU_IMPORTER_DSN"], assume_role="jeju_importer"
            ).rollback(arguments.source, UUID(arguments.publication))
        except Exception as error:
            _print_status("Fail", arguments.source, type(error).__name__, "rollback", "")
            return 2
        _print_status("Pass", arguments.source, evidence="active pointer rollback")
        return 0
    matches = [source for source in catalog.sources if source.id == arguments.source]
    if not matches:
        _print_status("Fail", arguments.source, "SOURCE_UNKNOWN", "catalog lookup", "")
        return 2
    source = matches[0]
    if arguments.command == "inspect":
        status = "Pass" if source.license.status == "APPROVED" else "Fail"
        _print_status(
            status,
            source.id,
            "" if status == "Pass" else "SOURCE_PENDING",
            f"contract={source.contract_fingerprint}; license={source.license.status}",
            "약관·갱신주기는 production 전 재검토가 필요합니다.",
        )
        return 0 if status == "Pass" else 2
    readiness = SourcePreflight().check(source, dict(os.environ))
    if readiness.status != "PASS":
        _print_status(
            "Fail",
            source.id,
            ",".join(readiness.reason_codes),
            "; ".join(readiness.evidence),
            "preflight 실패로 네트워크를 호출하지 않았습니다.",
        )
        return 2
    _print_status(
        "Pass",
        source.id,
        evidence=(
            "; ".join(readiness.evidence)
            + "; secrets="
            + ",".join(readiness.permitted_secret_names)
        ),
        risk="live endpoint 호출은 수행하지 않은 preflight입니다.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
