-- v0.4 제주 전역 생성·판정·실시간 기능을 위한 append-only projection 확장.

ALTER TABLE travel_projection.scheduled_stop_time
  DROP CONSTRAINT scheduled_stop_time_check,
  ADD COLUMN arrival_day_offset smallint NOT NULL DEFAULT 0
    CHECK (arrival_day_offset BETWEEN 0 AND 3),
  ADD COLUMN departure_day_offset smallint NOT NULL DEFAULT 0
    CHECK (departure_day_offset BETWEEN 0 AND 3),
  ADD CONSTRAINT stop_time_day_offset_order CHECK (
    departure_day_offset > arrival_day_offset
    OR (departure_day_offset = arrival_day_offset AND departure_at >= arrival_at)
  );

ALTER TABLE travel_projection.scheduled_trip
  ADD COLUMN timetable_effective_from date,
  ADD COLUMN timetable_effective_to date,
  ADD CONSTRAINT timetable_effective_range CHECK (
    timetable_effective_to IS NULL
    OR timetable_effective_from IS NULL
    OR timetable_effective_to >= timetable_effective_from
  );

ALTER TABLE travel_projection.place_entrance
  ADD COLUMN last_verified_at timestamptz,
  ADD COLUMN verification_expires_at timestamptz,
  ADD COLUMN supported_modes text[] NOT NULL DEFAULT ARRAY[]::text[];

ALTER TABLE travel_projection.restroom_fact
  ADD COLUMN address_precision text,
  ADD COLUMN location_confidence double precision
    CHECK (location_confidence BETWEEN 0 AND 1),
  ADD COLUMN opens_at time,
  ADD COLUMN closes_at time;

ALTER TABLE travel_projection.source_coverage
  DROP CONSTRAINT source_coverage_pkey,
  ADD COLUMN region_code text NOT NULL DEFAULT 'JEJU_ALL',
  ADD COLUMN grid_id text NOT NULL DEFAULT 'ALL',
  ADD COLUMN service_date_from date,
  ADD COLUMN service_date_to date,
  ADD COLUMN measured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  ADD COLUMN blocking_reason text,
  ADD PRIMARY KEY (publication_id, capability, region_code, grid_id),
  ADD CONSTRAINT source_coverage_date_range CHECK (
    service_date_to IS NULL
    OR service_date_from IS NULL
    OR service_date_to >= service_date_from
  );

CREATE TABLE travel_projection.place_opening_rule (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  service_day smallint CHECK (service_day BETWEEN 1 AND 7),
  valid_from date,
  valid_to date,
  period_kind text NOT NULL CHECK (period_kind IN ('OPEN', 'BREAK')),
  opens_minute smallint NOT NULL CHECK (opens_minute BETWEEN 0 AND 1439),
  closes_minute smallint NOT NULL CHECK (closes_minute BETWEEN 0 AND 1439),
  closes_day_offset smallint NOT NULL DEFAULT 0 CHECK (closes_day_offset BETWEEN 0 AND 1),
  last_admission_minute smallint CHECK (last_admission_minute BETWEEN 0 AND 1439),
  last_order_minute smallint CHECK (last_order_minute BETWEEN 0 AND 1439),
  normalization_status text NOT NULL
    CHECK (normalization_status IN ('VERIFIED', 'PARTIAL', 'UNKNOWN', 'CONFLICTED')),
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
  CHECK (closes_day_offset = 1 OR closes_minute > opens_minute)
);

CREATE TABLE travel_projection.place_schedule_exception (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  exception_date date NOT NULL,
  exception_type text NOT NULL CHECK (exception_type IN ('CLOSED', 'SPECIAL_HOURS')),
  opens_minute smallint CHECK (opens_minute BETWEEN 0 AND 1439),
  closes_minute smallint CHECK (closes_minute BETWEEN 0 AND 1439),
  closes_day_offset smallint NOT NULL DEFAULT 0 CHECK (closes_day_offset BETWEEN 0 AND 1),
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.service_calendar_exception (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  service_id text NOT NULL,
  exception_date date NOT NULL,
  exception_type text NOT NULL CHECK (exception_type IN ('ADDED', 'REMOVED')),
  holiday_name text,
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, service_id, exception_date)
);

CREATE TABLE travel_projection.timetable_notice (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  provider_notice_id text NOT NULL,
  title text NOT NULL,
  published_on date NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  attachment_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, provider_notice_id),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE travel_projection.route_service_exception (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  route_fact_id text NOT NULL,
  notice_fact_id text NOT NULL,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz,
  exception_type text NOT NULL
    CHECK (exception_type IN ('DETOUR', 'STOP_SKIPPED', 'SUSPENDED', 'TIMETABLE_CHANGED')),
  affected_stop_fact_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
  replacement_publication_id uuid REFERENCES source_admin.publication(publication_id),
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  CHECK (ends_at IS NULL OR ends_at >= starts_at)
);

CREATE TABLE travel_projection.holiday_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  holiday_date date NOT NULL,
  name text NOT NULL,
  is_public_institution_holiday boolean NOT NULL,
  date_kind text NOT NULL,
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, holiday_date, name)
);

CREATE TABLE travel_projection.taxi_fare_policy_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  vehicle_type text NOT NULL CHECK (vehicle_type IN ('SMALL', 'STANDARD', 'LARGE')),
  effective_from date NOT NULL,
  effective_to date,
  base_fare_krw integer NOT NULL CHECK (base_fare_krw >= 0),
  base_distance_meters integer NOT NULL CHECK (base_distance_meters > 0),
  distance_unit_meters integer NOT NULL CHECK (distance_unit_meters > 0),
  distance_unit_fare_krw integer NOT NULL CHECK (distance_unit_fare_krw >= 0),
  time_speed_threshold_kph integer NOT NULL CHECK (time_speed_threshold_kph > 0),
  time_unit_seconds integer NOT NULL CHECK (time_unit_seconds > 0),
  time_unit_fare_krw integer NOT NULL CHECK (time_unit_fare_krw >= 0),
  long_distance_threshold_meters integer,
  long_distance_unit_fare_krw integer,
  night_start time,
  night_end time,
  night_surcharge_ratio double precision CHECK (night_surcharge_ratio BETWEEN 0 AND 1),
  call_fee_max_krw integer CHECK (call_fee_max_krw >= 0),
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE travel_projection.policy_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  policy_key text NOT NULL,
  policy_version text NOT NULL,
  effective_from date NOT NULL,
  effective_to date,
  value jsonb NOT NULL,
  unit text,
  is_estimated boolean NOT NULL,
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, policy_key, policy_version),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

DO $$
DECLARE projection_table text;
BEGIN
  FOREACH projection_table IN ARRAY ARRAY[
    'place_opening_rule', 'place_schedule_exception', 'service_calendar_exception',
    'timetable_notice', 'route_service_exception', 'holiday_fact',
    'taxi_fare_policy_fact', 'policy_fact'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER %I_immutable BEFORE UPDATE OR DELETE ON travel_projection.%I '
      'FOR EACH ROW EXECUTE FUNCTION travel_projection.reject_mutation()',
      projection_table, projection_table
    );
  END LOOP;
END
$$;

CREATE VIEW travel_read.active_place_opening_rule AS
SELECT value.* FROM travel_projection.place_opening_rule value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_place_schedule_exception AS
SELECT value.* FROM travel_projection.place_schedule_exception value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_service_calendar_exception AS
SELECT value.* FROM travel_projection.service_calendar_exception value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_timetable_notice AS
SELECT value.* FROM travel_projection.timetable_notice value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_route_service_exception AS
SELECT value.* FROM travel_projection.route_service_exception value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_holiday AS
SELECT value.* FROM travel_projection.holiday_fact value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_taxi_fare_policy AS
SELECT value.* FROM travel_projection.taxi_fare_policy_fact value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_policy_fact AS
SELECT value.* FROM travel_projection.policy_fact value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_source_coverage AS
SELECT value.* FROM travel_projection.source_coverage value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT, INSERT ON
  travel_projection.place_opening_rule,
  travel_projection.place_schedule_exception,
  travel_projection.service_calendar_exception,
  travel_projection.timetable_notice,
  travel_projection.route_service_exception,
  travel_projection.holiday_fact,
  travel_projection.taxi_fare_policy_fact,
  travel_projection.policy_fact
TO jeju_importer;

REVOKE UPDATE, DELETE ON
  travel_projection.place_opening_rule,
  travel_projection.place_schedule_exception,
  travel_projection.service_calendar_exception,
  travel_projection.timetable_notice,
  travel_projection.route_service_exception,
  travel_projection.holiday_fact,
  travel_projection.taxi_fare_policy_fact,
  travel_projection.policy_fact
FROM jeju_importer;

GRANT SELECT ON
  travel_read.active_place_opening_rule,
  travel_read.active_place_schedule_exception,
  travel_read.active_service_calendar_exception,
  travel_read.active_timetable_notice,
  travel_read.active_route_service_exception,
  travel_read.active_holiday,
  travel_read.active_taxi_fare_policy,
  travel_read.active_policy_fact,
  travel_read.active_source_coverage
TO jeju_runtime;
