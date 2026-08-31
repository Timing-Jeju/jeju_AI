"""DB migration 권한과 불변성 정적 계약 테스트."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_role_can_only_select_travel_read_views() -> None:
    """MCP 런타임 역할은 수집 관리 테이블을 직접 읽을 수 없어야 한다."""

    sql = (ROOT / "db/migrations/0001_initial.sql").read_text()
    assert "GRANT USAGE ON SCHEMA travel_read TO jeju_runtime" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA travel_read TO jeju_runtime" in sql
    assert "source_admin TO jeju_runtime" not in sql
    assert "travel_projection TO jeju_runtime" not in sql


def test_raw_objects_and_publications_have_immutable_triggers() -> None:
    """원본 객체와 publication은 수정하거나 삭제할 수 없어야 한다."""

    sql = (ROOT / "db/migrations/0001_initial.sql").read_text()
    assert "raw_object_immutable" in sql
    assert "publication_immutable" in sql
    assert "BEFORE UPDATE OR DELETE" in sql


def test_importer_can_advance_lifecycle_but_not_mutate_immutable_data() -> None:
    """importer는 acquisition·active snapshot만 갱신하고 raw·publication은 수정하지 못해야 한다."""

    sql = (ROOT / "db/migrations/0002_importer_lifecycle_permissions.sql").read_text()
    assert "ON source_admin.acquisition TO jeju_importer" in sql
    assert "ON source_admin.active_snapshot TO jeju_importer" in sql
    assert "GRANT UPDATE ON source_admin.raw_object" not in sql
    assert "GRANT UPDATE ON source_admin.publication" not in sql


def test_v04_migration_models_timetable_exceptions_and_policy_facts() -> None:
    """v0.5 저장 구조는 자정 이후 시간표·시행일 예외·정책 fact를 표현해야 한다."""

    sql = (ROOT / "db/migrations/0003_v04_evaluation_and_data_capabilities.sql").read_text()
    assert "arrival_day_offset" in sql
    assert "service_calendar_exception" in sql
    assert "timetable_notice" in sql
    assert "route_service_exception" in sql
    assert "taxi_fare_policy_fact" in sql
    assert "policy_fact" in sql


def test_v04_source_coverage_is_region_and_date_scoped() -> None:
    """capability coverage는 제주 권역·격자·서비스 날짜 범위를 구분해야 한다."""

    sql = (ROOT / "db/migrations/0003_v04_evaluation_and_data_capabilities.sql").read_text()
    for field in (
        "region_code",
        "grid_id",
        "service_date_from",
        "service_date_to",
        "measured_at",
        "blocking_reason",
    ):
        assert field in sql


def test_predata_migration_adds_boundary_and_bus_fare_provenance() -> None:
    """첫 데이터 전 migration은 제주 polygon과 버스요금 active fact를 제공해야 한다."""

    sql = (ROOT / "db/migrations/0004_predata_integrity.sql").read_text()
    assert "service_area_boundary" in sql
    assert "geometry(MultiPolygon, 4326)" in sql
    assert "active_service_area_boundary" in sql
    assert "bus_fare_policy_fact" in sql
    assert "active_bus_fare_policy" in sql


def test_scope_coverage_migration_grants_importer_read_only_denominator_access() -> None:
    """coverage 측정 importer는 active scope 분모를 읽되 projection 수정 권한은 얻지 않아야 한다."""

    sql = (ROOT / "db/migrations/0007_scope_coverage_permissions.sql").read_text()
    assert "GRANT USAGE ON SCHEMA travel_read TO jeju_importer" in sql
    assert "GRANT SELECT ON travel_read.active_service_scope_member TO jeju_importer" in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql


def test_active_coverage_range_migration_preserves_immutable_rows() -> None:
    """active coverage 날짜 확장은 기존 행 수정 없이 새 날짜 범위를 append해야 한다."""

    sql = (ROOT / "db/migrations/0008_append_active_coverage_ranges.sql").read_text()
    assert "coverage_id bigint GENERATED ALWAYS AS IDENTITY" in sql
    assert "UNIQUE NULLS NOT DISTINCT" in sql
    assert "UPDATE travel_projection.source_coverage" not in sql
    assert "DELETE FROM travel_projection.source_coverage" not in sql


def test_stop_time_view_is_rebuilt_after_day_offset_columns_are_added() -> None:
    """런타임 시간표 view는 migration 뒤 자정 이월 offset 열을 실제로 노출해야 한다."""

    sql = (ROOT / "db/migrations/0009_rebuild_active_stop_time_view.sql").read_text()
    assert "DROP VIEW travel_read.active_stop_time" in sql
    assert "CREATE VIEW travel_read.active_stop_time" in sql
    assert "arrival_day_offset" in sql
    assert "departure_day_offset" in sql
    assert "GRANT SELECT ON travel_read.active_stop_time TO jeju_runtime" in sql


def test_scheduled_trip_view_is_rebuilt_after_effective_date_columns_are_added() -> None:
    """런타임 trip view는 migration 뒤 시간표 시행 시작·종료일을 실제로 노출해야 한다."""

    sql = (ROOT / "db/migrations/0010_rebuild_active_scheduled_trip_view.sql").read_text()
    assert "DROP VIEW travel_read.active_scheduled_trip" in sql
    assert "CREATE VIEW travel_read.active_scheduled_trip" in sql
    assert "timetable_effective_from" in sql
    assert "timetable_effective_to" in sql
    assert "GRANT SELECT ON travel_read.active_scheduled_trip TO jeju_runtime" in sql


def test_place_entrance_view_is_rebuilt_after_routing_columns_are_added() -> None:
    """런타임 입구 view는 검증시각·만료시각·지원 이동수단 열을 실제로 노출해야 한다."""

    sql = (ROOT / "db/migrations/0011_rebuild_active_place_entrance_view.sql").read_text()
    assert "DROP VIEW travel_read.active_place_entrance" in sql
    assert "CREATE VIEW travel_read.active_place_entrance" in sql
    assert "last_verified_at" in sql
    assert "verification_expires_at" in sql
    assert "supported_modes" in sql
    assert "GRANT SELECT ON travel_read.active_place_entrance TO jeju_runtime" in sql


def test_active_route_stop_view_prefers_the_dedicated_tago_snapshot() -> None:
    """전역 TAGO 경유정류장과 시간표 fallback이 겹치면 전용 source 한 행만 노출해야 한다."""

    sql = (ROOT / "db/migrations/0012_deduplicate_active_route_stops.sql").read_text()
    assert "DROP VIEW travel_read.active_bus_route_stop" in sql
    assert "DISTINCT ON" in sql
    assert "tago.bus-route-stops" in sql
    assert "CREATE VIEW travel_read.active_bus_route_stop" in sql
    assert "GRANT SELECT ON travel_read.active_bus_route_stop TO jeju_runtime" in sql


def test_active_coverage_view_rejects_route_catalog_as_confirmed_identity() -> None:
    """활성 coverage는 경유정류장 catalog를 confirmed stop identity 근거로 노출하지 않아야 한다."""

    sql = (ROOT / "db/migrations/0014_filter_active_coverage_sources.sql").read_text()
    assert "DROP VIEW travel_read.active_source_coverage" in sql
    assert "confirmed_stop_mapping_ready" in sql
    assert "transport.stop-identity-map" in sql
    assert "CREATE VIEW travel_read.active_source_coverage" in sql


def test_weekly_place_closure_migration_is_immutable_and_runtime_readable() -> None:
    """반복 휴무 fact는 append-only projection과 active runtime view로 제공돼야 한다."""

    sql = (ROOT / "db/migrations/0016_place_weekly_closure.sql").read_text()
    assert "CREATE TABLE travel_projection.place_weekly_closure" in sql
    assert "place_weekly_closure_immutable" in sql
    assert "CREATE VIEW travel_read.active_place_weekly_closure" in sql
    assert "GRANT SELECT, INSERT ON travel_projection.place_weekly_closure" in sql
    assert "GRANT SELECT ON travel_read.active_place_weekly_closure TO jeju_runtime" in sql


def test_opening_observation_migration_is_immutable_and_runtime_readable() -> None:
    """장소별 상세소개 관측 상태는 원문 없이 append-only projection과 runtime view로 남아야 한다."""

    sql = (ROOT / "db/migrations/0017_place_opening_observation.sql").read_text()
    assert "CREATE TABLE travel_projection.place_opening_observation" in sql
    assert "place_opening_observation_immutable" in sql
    assert "UNIQUE (publication_id, place_fact_id)" in sql
    assert "CREATE VIEW travel_read.active_place_opening_observation" in sql
    assert "GRANT SELECT, INSERT ON travel_projection.place_opening_observation" in sql
    assert "GRANT SELECT ON travel_read.active_place_opening_observation TO jeju_runtime" in sql


def test_restaurant_dietary_migration_is_immutable_and_runtime_readable() -> None:
    """메뉴 식이 fact는 명시적 부재 배열·만료를 append-only runtime view로 제공해야 한다."""

    sql = (ROOT / "db/migrations/0018_restaurant_dietary_fact.sql").read_text()
    assert "CREATE TABLE travel_projection.restaurant_dietary_fact" in sql
    assert "verified_free_from_allergens text[]" in sql
    assert "verified_excludes_foods text[]" in sql
    assert "verification_expires_at timestamptz" in sql
    assert "restaurant_dietary_fact_immutable" in sql
    assert "CREATE VIEW travel_read.active_restaurant_dietary_fact" in sql
    assert "GRANT SELECT, INSERT ON travel_projection.restaurant_dietary_fact" in sql
    assert "GRANT SELECT ON travel_read.active_restaurant_dietary_fact TO jeju_runtime" in sql
