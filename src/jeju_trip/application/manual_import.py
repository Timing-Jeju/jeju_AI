"""승인된 수동 CSV를 raw-first 검증 뒤 append-only publication으로 발행한다."""

from __future__ import annotations

import csv
import io
import json
import tomllib
import zipfile
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from jeju_trip.application.refresh_service import RefreshOutcome
from jeju_trip.infrastructure.normalization_spool import NormalizationSpool
from jeju_trip.infrastructure.official_timetable_bundle import build_weekday_timetable_bundle
from jeju_trip.infrastructure.projection_publisher import (
    OpeningHoursBundle,
    ScopeManifestBundle,
    TimetableBundle,
)
from jeju_trip.infrastructure.public_data_normalizers import (
    NormalizedBatch,
    normalize_bus_route_stops,
    normalize_itinerary_template_candidates,
    normalize_itinerary_template_steps,
    normalize_place_opening_rules,
    normalize_place_schedule_exceptions,
    normalize_route_service_exceptions,
    normalize_scheduled_stop_times,
    normalize_scheduled_trips,
    normalize_scope_members,
    normalize_service_calendar_exceptions,
    normalize_service_calendars,
    normalize_timetable_notices,
)
from jeju_trip.infrastructure.sgis_boundary import (
    assert_official_sgis_boundary_checksum,
    normalize_sgis_jeju_boundary_zip,
)
from jeju_trip.infrastructure.source_catalog import TravelSourceContract
from jeju_trip.infrastructure.timetable_xlsx import load_official_timetable_zip

TIMETABLE_FILES = (
    "bus-route-stops.csv",
    "service-calendars.csv",
    "scheduled-trips.csv",
    "scheduled-stop-times.csv",
    "service-calendar-exceptions.csv",
    "timetable-notices.csv",
    "route-service-exceptions.csv",
)
OPENING_HOURS_FILES = ("place-opening-rules.csv", "place-schedule-exceptions.csv")
SCOPE_MANIFEST_FILES = (
    "service-scope-members.csv",
    "itinerary-template-steps.csv",
    "itinerary-template-candidates.csv",
)


def validate_timetable_bundle(bundle: TimetableBundle) -> None:
    """시간표 묶음 내부의 핵심 참조와 시각 순서를 publication 전에 검증한다."""

    route_keys = {(item.route_fact_id, item.direction_text) for item in bundle.route_stops}
    route_stops: dict[str, set[str]] = {}
    route_sequences: set[tuple[str, str, int]] = set()
    for item in bundle.route_stops:
        key = (item.route_fact_id, item.direction_text, item.route_sequence)
        if key in route_sequences:
            raise ValueError("TIMETABLE_ROUTE_SEQUENCE_DUPLICATED")
        route_sequences.add(key)
        route_stops.setdefault(item.route_fact_id, set()).add(item.stop_fact_id)

    service_ids = {item.service_id for item in bundle.service_calendars}
    if len(service_ids) != len(bundle.service_calendars):
        raise ValueError("TIMETABLE_SERVICE_ID_DUPLICATED")
    trips = {item.trip_id: item for item in bundle.trips}
    if len(trips) != len(bundle.trips):
        raise ValueError("TIMETABLE_TRIP_ID_DUPLICATED")
    for trip in bundle.trips:
        if (trip.route_fact_id, trip.direction_text) not in route_keys:
            raise ValueError("TIMETABLE_TRIP_ROUTE_DIRECTION_MISSING")
        if trip.service_id not in service_ids:
            raise ValueError("TIMETABLE_TRIP_SERVICE_MISSING")

    stop_times_by_trip: dict[str, list[Any]] = {}
    for item in bundle.stop_times:
        if item.trip_id not in trips:
            raise ValueError("TIMETABLE_STOP_TIME_TRIP_MISSING")
        trip = trips[item.trip_id]
        if item.stop_id not in route_stops.get(trip.route_fact_id, set()):
            raise ValueError("TIMETABLE_STOP_NOT_ON_ROUTE")
        stop_times_by_trip.setdefault(item.trip_id, []).append(item)
    for trip_id in trips:
        values = sorted(stop_times_by_trip.get(trip_id, []), key=lambda item: item.stop_sequence)
        if len(values) < 2:
            raise ValueError("TIMETABLE_TRIP_STOPS_INSUFFICIENT")
        if len({item.stop_sequence for item in values}) != len(values):
            raise ValueError("TIMETABLE_STOP_SEQUENCE_DUPLICATED")
        previous_departure = -1
        for item in values:
            arrival = (
                item.arrival_day_offset * 1440 + item.arrival_at.hour * 60 + item.arrival_at.minute
            )
            departure = (
                item.departure_day_offset * 1440
                + item.departure_at.hour * 60
                + item.departure_at.minute
            )
            if arrival < previous_departure or departure < arrival:
                raise ValueError("TIMETABLE_TRIP_TIME_ORDER_INVALID")
            previous_departure = departure

    for item in bundle.service_exceptions:
        if item.service_id not in service_ids:
            raise ValueError("TIMETABLE_EXCEPTION_SERVICE_MISSING")
    notice_ids = {item.fact_id for item in bundle.notices}
    for item in bundle.route_exceptions:
        if item.route_fact_id not in route_stops:
            raise ValueError("TIMETABLE_EXCEPTION_ROUTE_MISSING")
        if item.notice_fact_id not in notice_ids:
            raise ValueError("TIMETABLE_EXCEPTION_NOTICE_MISSING")
        if not set(item.affected_stop_fact_ids) <= route_stops[item.route_fact_id]:
            raise ValueError("TIMETABLE_EXCEPTION_STOP_MISSING")


def validate_scope_manifest_bundle(bundle: ScopeManifestBundle) -> None:
    """scope·template 참조와 전략별 순번·후보 우선순위의 유일성을 검증한다."""

    if not bundle.members or not bundle.steps or not bundle.candidates:
        raise ValueError("SCOPE_MANIFEST_SECTION_EMPTY")
    member_scopes = {(item.region_code, item.grid_id) for item in bundle.members}
    step_scopes = {(item.region_code, item.grid_id) for item in bundle.steps}
    candidate_scopes = {(item.region_code, item.grid_id) for item in bundle.candidates}
    if member_scopes != step_scopes or member_scopes != candidate_scopes:
        raise ValueError("SCOPE_MANIFEST_SCOPE_MISMATCH")
    place_ids = {
        (item.region_code, item.grid_id, item.member_id)
        for item in bundle.members
        if item.member_type == "PLACE"
    }
    groups = {
        (item.region_code, item.grid_id, item.candidate_group) for item in bundle.steps
    }
    if any(
        (item.region_code, item.grid_id, item.candidate_group) not in groups
        for item in bundle.candidates
    ):
        raise ValueError("SCOPE_MANIFEST_CANDIDATE_GROUP_UNKNOWN")
    if any(
        (item.region_code, item.grid_id, item.place_fact_id) not in place_ids
        for item in bundle.candidates
    ):
        raise ValueError("SCOPE_MANIFEST_CANDIDATE_PLACE_NOT_MEMBER")
    step_keys = {
        (item.region_code, item.grid_id, item.strategy, item.sequence) for item in bundle.steps
    }
    if len(step_keys) != len(bundle.steps):
        raise ValueError("SCOPE_MANIFEST_STEP_DUPLICATED")
    candidate_keys = {
        (item.region_code, item.grid_id, item.candidate_group, item.priority)
        for item in bundle.candidates
    }
    if len(candidate_keys) != len(bundle.candidates):
        raise ValueError("SCOPE_MANIFEST_PRIORITY_DUPLICATED")
    groups_with_candidates = {
        (item.region_code, item.grid_id, item.candidate_group) for item in bundle.candidates
    }
    if not groups <= groups_with_candidates:
        raise ValueError("SCOPE_MANIFEST_CANDIDATE_GROUP_EMPTY")


class ManualCsvImportService:
    """파일 경로나 행 원문을 로그에 남기지 않는 수동 publication 조정기."""

    def __init__(self, raw_store: Any, source_admin: Any) -> None:
        self._raw_store = raw_store
        self._source_admin = source_admin

    def import_single(
        self,
        contract: TravelSourceContract,
        path: Path,
        source_date: date,
        normalizer: Callable[[list[dict[str, Any]]], NormalizedBatch[Any]],
        publisher: Callable[[Any, tuple[Any, ...]], Any],
        *,
        scope_expansion_from_rows: int | None = None,
    ) -> RefreshOutcome:
        files = {"records.csv": path}
        rows_by_name, archive = self._read_and_archive(contract, files)
        normalized = normalizer(rows_by_name["records.csv"])
        return self._validate_and_publish(
            contract,
            source_date,
            archive,
            len(rows_by_name["records.csv"]),
            normalized.records,
            normalized.rejections,
            publisher,
            scope_expansion_from_rows=scope_expansion_from_rows,
        )

    def import_json(
        self,
        contract: TravelSourceContract,
        path: Path,
        source_date: date,
        normalizer: Callable[[dict[str, Any]], NormalizedBatch[Any]],
        publisher: Callable[[Any, tuple[Any, ...]], Any],
    ) -> RefreshOutcome:
        """승인된 단일 JSON 원본도 CSV와 같은 raw-first 흐름으로 발행한다."""

        if contract.license.status != "APPROVED":
            raise ValueError(f"SOURCE_NOT_APPROVED:{contract.id}")
        if contract.acquisition.mode != "MANUAL":
            raise ValueError(f"SOURCE_NOT_MANUAL:{contract.id}")
        raw = path.read_bytes()
        if len(raw) > contract.acquisition.maximum_response_bytes:
            raise ValueError("MANUAL_SOURCE_TOO_LARGE")
        try:
            document = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("MANUAL_JSON_INVALID") from error
        if not isinstance(document, dict):
            raise ValueError("MANUAL_JSON_ROOT_INVALID")
        normalized = normalizer(document)
        return self._validate_and_publish(
            contract,
            source_date,
            raw,
            1,
            normalized.records,
            normalized.rejections,
            publisher,
            extension="json",
            content_type="application/json",
        )

    def import_boundary_zip(
        self,
        contract: TravelSourceContract,
        path: Path,
        source_date: date,
        publisher: Callable[[Any, tuple[Any, ...]], Any],
    ) -> RefreshOutcome:
        """공식 ZIP 자체를 checksum 검증·보관한 뒤 제주 경계 한 건만 발행한다."""

        if contract.license.status != "APPROVED":
            raise ValueError(f"SOURCE_NOT_APPROVED:{contract.id}")
        if contract.acquisition.mode != "MANUAL" or contract.acquisition.format != "ZIP":
            raise ValueError(f"SOURCE_NOT_MANUAL_ZIP:{contract.id}")
        raw = path.read_bytes()
        if len(raw) > contract.acquisition.maximum_response_bytes:
            raise ValueError("MANUAL_SOURCE_TOO_LARGE")
        assert_official_sgis_boundary_checksum(raw)
        return self._validate_and_publish(
            contract,
            source_date,
            raw,
            1,
            (),
            (),
            publisher,
            extension="zip",
            content_type="application/zip",
            normalizer_after_store=lambda: normalize_sgis_jeju_boundary_zip(
                raw, source_date=source_date
            ),
        )

    def import_toml(
        self,
        contract: TravelSourceContract,
        path: Path,
        source_date: date,
        validator: Callable[[dict[str, Any]], Any],
        publisher: Callable[[Any, tuple[Any, ...]], Any],
    ) -> RefreshOutcome:
        """공식 요금 TOML을 하나의 versioned policy fact 묶음으로 발행한다."""

        if contract.license.status != "APPROVED":
            raise ValueError(f"SOURCE_NOT_APPROVED:{contract.id}")
        raw = path.read_bytes()
        if len(raw) > contract.acquisition.maximum_response_bytes:
            raise ValueError("MANUAL_SOURCE_TOO_LARGE")
        try:
            document = tomllib.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError("MANUAL_TOML_INVALID") from error
        policy = validator(document)
        return self._validate_and_publish(
            contract,
            source_date,
            raw,
            1,
            (policy,),
            (),
            publisher,
            extension="toml",
            content_type="application/toml",
        )

    def import_timetable(
        self,
        contract: TravelSourceContract,
        paths: Mapping[str, Path],
        source_date: date,
        publisher: Callable[[Any, TimetableBundle], Any],
    ) -> RefreshOutcome:
        if set(paths) != set(TIMETABLE_FILES):
            raise ValueError("TIMETABLE_BUNDLE_FILES_INVALID")
        rows, archive = self._read_and_archive(contract, paths)
        normalized_route_stops = normalize_bus_route_stops(rows["bus-route-stops.csv"])
        normalized_calendars = normalize_service_calendars(rows["service-calendars.csv"])
        normalized_trips = normalize_scheduled_trips(rows["scheduled-trips.csv"])
        normalized_stop_times = normalize_scheduled_stop_times(rows["scheduled-stop-times.csv"])
        normalized_service_exceptions = normalize_service_calendar_exceptions(
            rows["service-calendar-exceptions.csv"]
        )
        normalized_notices = normalize_timetable_notices(rows["timetable-notices.csv"])
        normalized_route_exceptions = normalize_route_service_exceptions(
            rows["route-service-exceptions.csv"]
        )
        batches = (
            normalized_route_stops,
            normalized_calendars,
            normalized_trips,
            normalized_stop_times,
            normalized_service_exceptions,
            normalized_notices,
            normalized_route_exceptions,
        )
        records = tuple(record for batch in batches for record in batch.records)
        rejections = tuple(rejection for batch in batches for rejection in batch.rejections)
        bundle = TimetableBundle(
            route_stops=normalized_route_stops.records,
            service_calendars=normalized_calendars.records,
            trips=normalized_trips.records,
            stop_times=normalized_stop_times.records,
            service_exceptions=normalized_service_exceptions.records,
            notices=normalized_notices.records,
            route_exceptions=normalized_route_exceptions.records,
        )
        validate_timetable_bundle(bundle)
        return self._validate_and_publish(
            contract,
            source_date,
            archive,
            sum(len(items) for items in rows.values()),
            records,
            rejections,
            lambda acquisition_id, _: publisher(acquisition_id, bundle),
        )

    def import_official_timetable(
        self,
        contract: TravelSourceContract,
        path: Path,
        target_date: date,
        publisher: Callable[[Any, TimetableBundle], Any],
    ) -> RefreshOutcome:
        """공식 XLSX raw ZIP을 먼저 보관한 뒤 목표 평일 bundle을 발행한다."""

        if contract.license.status != "APPROVED":
            raise ValueError(f"SOURCE_NOT_APPROVED:{contract.id}")
        if contract.acquisition.mode != "MANUAL" or contract.acquisition.format != "ZIP":
            raise ValueError(f"SOURCE_NOT_MANUAL_ZIP:{contract.id}")
        raw = path.read_bytes()
        if len(raw) > contract.acquisition.maximum_response_bytes:
            raise ValueError("MANUAL_SOURCE_TOO_LARGE")
        holder: dict[str, TimetableBundle] = {}

        def normalize_after_store() -> NormalizedBatch[Any]:
            loaded = load_official_timetable_zip(io.BytesIO(raw))
            bundle = build_weekday_timetable_bundle(loaded, target_date)
            validate_timetable_bundle(bundle)
            holder["bundle"] = bundle
            records = (
                *bundle.route_stops,
                *bundle.service_calendars,
                *bundle.trips,
                *bundle.stop_times,
                *bundle.service_exceptions,
                *bundle.notices,
                *bundle.route_exceptions,
            )
            return NormalizedBatch(records=records, rejections=())

        def publish(acquisition_id: Any, _: tuple[Any, ...]) -> Any:
            bundle = holder.get("bundle")
            if bundle is None:
                raise RuntimeError("TIMETABLE_BUNDLE_NOT_NORMALIZED")
            return publisher(acquisition_id, bundle)

        return self._validate_and_publish(
            contract,
            target_date,
            raw,
            1,
            (),
            (),
            publish,
            extension="zip",
            content_type="application/zip",
            normalizer_after_store=normalize_after_store,
        )

    def import_opening_hours(
        self,
        contract: TravelSourceContract,
        paths: Mapping[str, Path],
        source_date: date,
        publisher: Callable[[Any, OpeningHoursBundle], Any],
    ) -> RefreshOutcome:
        if set(paths) != set(OPENING_HOURS_FILES):
            raise ValueError("OPENING_HOURS_BUNDLE_FILES_INVALID")
        rows, archive = self._read_and_archive(contract, paths)
        rules = normalize_place_opening_rules(rows["place-opening-rules.csv"])
        exceptions = normalize_place_schedule_exceptions(rows["place-schedule-exceptions.csv"])
        records = (*rules.records, *exceptions.records)
        rejections = (*rules.rejections, *exceptions.rejections)
        bundle = OpeningHoursBundle(rules.records, exceptions.records)
        return self._validate_and_publish(
            contract,
            source_date,
            archive,
            sum(len(items) for items in rows.values()),
            records,
            rejections,
            lambda acquisition_id, _: publisher(acquisition_id, bundle),
        )

    def import_scope_manifest(
        self,
        contract: TravelSourceContract,
        paths: Mapping[str, Path],
        source_date: date,
        publisher: Callable[[Any, ScopeManifestBundle], Any],
    ) -> RefreshOutcome:
        """scope 구성원·template step·후보를 단일 raw ZIP으로 검증한다."""

        if set(paths) != set(SCOPE_MANIFEST_FILES):
            raise ValueError("SCOPE_MANIFEST_BUNDLE_FILES_INVALID")
        rows, archive = self._read_and_archive(contract, paths)
        members = normalize_scope_members(rows["service-scope-members.csv"])
        steps = normalize_itinerary_template_steps(rows["itinerary-template-steps.csv"])
        candidates = normalize_itinerary_template_candidates(
            rows["itinerary-template-candidates.csv"]
        )
        records = (*members.records, *steps.records, *candidates.records)
        rejections = (*members.rejections, *steps.rejections, *candidates.rejections)
        bundle = ScopeManifestBundle(members.records, steps.records, candidates.records)
        validate_scope_manifest_bundle(bundle)
        return self._validate_and_publish(
            contract,
            source_date,
            archive,
            sum(len(items) for items in rows.values()),
            records,
            rejections,
            lambda acquisition_id, _: publisher(acquisition_id, bundle),
        )

    @staticmethod
    def _read_and_archive(
        contract: TravelSourceContract, paths: Mapping[str, Path]
    ) -> tuple[dict[str, list[dict[str, Any]]], bytes]:
        if contract.license.status != "APPROVED":
            raise ValueError(f"SOURCE_NOT_APPROVED:{contract.id}")
        if contract.acquisition.mode != "MANUAL":
            raise ValueError(f"SOURCE_NOT_MANUAL:{contract.id}")
        rows: dict[str, list[dict[str, Any]]] = {}
        archive_stream = io.BytesIO()
        total_bytes = 0
        with zipfile.ZipFile(archive_stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for logical_name in sorted(paths):
                raw = paths[logical_name].read_bytes()
                total_bytes += len(raw)
                if total_bytes > contract.acquisition.maximum_response_bytes:
                    raise ValueError("MANUAL_SOURCE_TOO_LARGE")
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError as error:
                    raise ValueError("MANUAL_SOURCE_ENCODING_INVALID") from error
                reader = csv.DictReader(io.StringIO(text, newline=""))
                if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise ValueError("MANUAL_CSV_HEADER_INVALID")
                rows[logical_name] = [dict(item) for item in reader]
                info = zipfile.ZipInfo(logical_name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, raw)
        return rows, archive_stream.getvalue()

    def _validate_and_publish(
        self,
        contract: TravelSourceContract,
        source_date: date,
        archive: bytes,
        raw_row_count: int,
        records: tuple[Any, ...],
        rejections: tuple[Any, ...],
        publisher: Callable[[Any, tuple[Any, ...]], Any],
        extension: str = "zip",
        content_type: str = "application/zip",
        normalizer_after_store: Callable[[], NormalizedBatch[Any]] | None = None,
        scope_expansion_from_rows: int | None = None,
    ) -> RefreshOutcome:
        registrations: list[Any] = []

        def register(pointer: Any) -> None:
            registrations.append(
                self._source_admin.register_verified_raw(
                    contract,
                    pointer,
                    temporal_basis=contract.temporal.basis,
                    source_date=source_date,
                    observed_at=datetime.now(UTC),
                    raw_row_count=raw_row_count,
                    initial_status="ACQUIRED",
                )
            )

        self._raw_store.store_verified(
            contract,
            (archive,),
            extension,
            content_type,
            register,
        )
        if not registrations:
            raise RuntimeError("ACQUISITION_CALLBACK_NOT_CALLED")
        registration = registrations[0]
        if not registration.created and registration.status in {
            "VALIDATED",
            "STAGED",
            "PUBLISHED",
            "NO_CHANGE",
        }:
            return RefreshOutcome(contract.id, "NO_CHANGE", raw_row_count)

        if normalizer_after_store is not None:
            normalized = normalizer_after_store()
            records = normalized.records
            rejections = normalized.rejections

        accepted = len(records)
        rejected = len(rejections)
        total = accepted + rejected
        rejected_ratio = rejected / total if total else 1.0
        positioned = sum(getattr(record, "position", None) is not None for record in records)
        coordinate_ratio = positioned / accepted if accepted else 0.0
        quality = contract.quality
        previous = self._source_admin.previous_published_row_count(contract.id)
        scope_expansion_baseline_mismatch = (
            scope_expansion_from_rows is not None and previous != scope_expansion_from_rows
        )
        reviewed_scope_expansion = (
            scope_expansion_from_rows is not None
            and previous == scope_expansion_from_rows
            and raw_row_count > scope_expansion_from_rows
        )
        row_change_ratio = (
            abs(raw_row_count - previous) / previous if previous is not None and previous > 0 else 0
        )
        row_change_exceeded = (
            row_change_ratio > quality.maximum_row_change_ratio and not reviewed_scope_expansion
        )
        quality_failed = (
            raw_row_count < quality.minimum_rows
            or raw_row_count > quality.maximum_rows
            or rejected_ratio > quality.maximum_rejected_ratio
            or (
                quality.minimum_coordinate_ratio > 0
                and coordinate_ratio < quality.minimum_coordinate_ratio
            )
            or scope_expansion_baseline_mismatch
            or row_change_exceeded
        )
        if quality_failed:
            self._source_admin.mark_quality_failed(registration.acquisition_id, accepted, rejected)
            return RefreshOutcome(
                contract.id,
                "QUALITY_FAILED",
                raw_row_count,
                accepted,
                rejected,
                reason_code=(
                    "SOURCE_SCOPE_EXPANSION_BASELINE_MISMATCH"
                    if scope_expansion_baseline_mismatch
                    else (
                        "SOURCE_ROW_CHANGE_RATIO_EXCEEDED"
                        if row_change_exceeded
                        else "SOURCE_QUALITY_THRESHOLD_FAILED"
                    )
                ),
            )

        with NormalizationSpool() as spool:
            result = spool.write((record.model_dump(mode="json") for record in records), rejections)
            self._source_admin.mark_validated(
                registration.acquisition_id,
                result.accepted_count,
                result.rejected_count,
                result.normalized_checksum,
            )
            publication = publisher(registration.acquisition_id, records)
        return RefreshOutcome(
            contract.id,
            "STAGED" if getattr(publication, "created", True) else "NO_CHANGE",
            raw_row_count,
            accepted,
            rejected,
            result.normalized_checksum,
            publication_id=str(publication.publication_id),
            dataset_version=str(publication.dataset_version),
        )
