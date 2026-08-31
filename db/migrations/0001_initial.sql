CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jeju_migrator') THEN
    CREATE ROLE jeju_migrator NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jeju_importer') THEN
    CREATE ROLE jeju_importer NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'jeju_runtime') THEN
    CREATE ROLE jeju_runtime NOLOGIN;
  END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS source_admin AUTHORIZATION jeju_migrator;
CREATE SCHEMA IF NOT EXISTS travel_projection AUTHORIZATION jeju_migrator;
CREATE SCHEMA IF NOT EXISTS travel_read AUTHORIZATION jeju_migrator;

CREATE TABLE IF NOT EXISTS source_admin.schema_migration (
  version text PRIMARY KEY,
  checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE source_admin.data_source (
  source_id text PRIMARY KEY,
  provider text NOT NULL,
  source_name text NOT NULL,
  landing_url text NOT NULL,
  evidence_grade text NOT NULL,
  owner_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE source_admin.source_contract (
  contract_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES source_admin.data_source(source_id),
  contract_fingerprint text NOT NULL CHECK (contract_fingerprint ~ '^[0-9a-f]{64}$'),
  contract jsonb NOT NULL,
  normalization_schema_version text NOT NULL,
  license_status text NOT NULL CHECK (license_status IN ('APPROVED', 'PENDING', 'REJECTED')),
  reviewed_at timestamptz,
  registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (source_id, contract_fingerprint)
);

CREATE TABLE source_admin.refresh_run (
  refresh_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile text,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz,
  status text NOT NULL CHECK (status IN ('RUNNING', 'PASS', 'NO_CHANGE', 'FAIL'))
);

CREATE TABLE source_admin.refresh_run_item (
  refresh_run_id uuid NOT NULL REFERENCES source_admin.refresh_run(refresh_run_id),
  source_id text NOT NULL REFERENCES source_admin.data_source(source_id),
  status text NOT NULL,
  reason_code text,
  PRIMARY KEY (refresh_run_id, source_id)
);

CREATE TABLE source_admin.raw_object (
  source_id text NOT NULL REFERENCES source_admin.data_source(source_id),
  checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
  bucket text NOT NULL CHECK (bucket = 'jeju-private-raw'),
  object_key text NOT NULL,
  object_version_id text NOT NULL,
  content_type text NOT NULL,
  byte_length bigint NOT NULL CHECK (byte_length >= 0),
  checksum_algorithm text NOT NULL DEFAULT 'SHA256' CHECK (checksum_algorithm = 'SHA256'),
  retention_policy text NOT NULL,
  stored_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (source_id, checksum)
);

CREATE TABLE source_admin.acquisition (
  acquisition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES source_admin.data_source(source_id),
  contract_id uuid NOT NULL REFERENCES source_admin.source_contract(contract_id),
  raw_checksum text NOT NULL,
  temporal_basis text NOT NULL CHECK (temporal_basis IN ('SOURCE_DATE', 'OBSERVED_AT', 'RETRIEVED_AT')),
  source_date date,
  observed_at timestamptz,
  collected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  status text NOT NULL CHECK (status IN (
    'INCOMPLETE', 'ACQUIRED', 'PARSE_FAILED', 'QUALITY_FAILED', 'VALIDATED',
    'NO_CHANGE', 'PUBLISHED', 'PUBLICATION_FAILED'
  )),
  raw_row_count bigint,
  accepted_row_count bigint,
  rejected_row_count bigint,
  normalized_checksum text,
  normalization_schema_version text NOT NULL,
  validated_at timestamptz,
  FOREIGN KEY (source_id, raw_checksum) REFERENCES source_admin.raw_object(source_id, checksum),
  UNIQUE (source_id, raw_checksum, normalization_schema_version)
);

CREATE TABLE source_admin.quality_issue (
  issue_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  acquisition_id uuid NOT NULL REFERENCES source_admin.acquisition(acquisition_id),
  severity text NOT NULL CHECK (severity IN ('BLOCKING', 'WARNING')),
  reason_code text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE source_admin.rejected_row (
  rejected_row_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  acquisition_id uuid NOT NULL REFERENCES source_admin.acquisition(acquisition_id),
  source_record_id text,
  field_name text,
  reason_code text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE source_admin.publication (
  publication_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES source_admin.data_source(source_id),
  acquisition_id uuid NOT NULL UNIQUE REFERENCES source_admin.acquisition(acquisition_id),
  dataset_version text NOT NULL,
  raw_checksum text NOT NULL,
  normalized_checksum text NOT NULL CHECK (normalized_checksum ~ '^[0-9a-f]{64}$'),
  normalization_schema_version text NOT NULL,
  temporal_basis text NOT NULL,
  source_date date,
  observed_at timestamptz,
  published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (source_id, dataset_version)
);

CREATE TABLE source_admin.active_snapshot (
  source_id text PRIMARY KEY REFERENCES source_admin.data_source(source_id),
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  activated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE source_admin.activation_event (
  activation_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES source_admin.data_source(source_id),
  previous_publication_id uuid REFERENCES source_admin.publication(publication_id),
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  activated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION source_admin.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'IMMUTABLE_TABLE:%', TG_TABLE_NAME;
END
$$;

CREATE TRIGGER raw_object_immutable
BEFORE UPDATE OR DELETE ON source_admin.raw_object
FOR EACH ROW EXECUTE FUNCTION source_admin.reject_mutation();

CREATE TRIGGER publication_immutable
BEFORE UPDATE OR DELETE ON source_admin.publication
FOR EACH ROW EXECUTE FUNCTION source_admin.reject_mutation();

CREATE TABLE travel_projection.place_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  source_id text NOT NULL,
  fact_id text NOT NULL,
  source_record_id text NOT NULL,
  data_as_of date NOT NULL,
  observed_at timestamptz NOT NULL,
  name text NOT NULL,
  category text NOT NULL,
  address text NOT NULL,
  position geography(Point, 4326) NOT NULL,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (publication_id, fact_id)
);
CREATE INDEX place_fact_position_gix ON travel_projection.place_fact USING gist(position);

CREATE TABLE travel_projection.place_opening_period (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  service_day smallint NOT NULL CHECK (service_day BETWEEN 1 AND 7),
  opens_at time NOT NULL,
  closes_at time NOT NULL,
  last_admission_at time,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  CHECK (closes_at > opens_at),
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.place_closed_date (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  closed_on date NOT NULL,
  reason text,
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.place_entrance (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  entrance_id text NOT NULL,
  entrance_type text NOT NULL CHECK (entrance_type IN ('pedestrian', 'vehicle', 'main', 'accessible')),
  position geography(Point, 4326) NOT NULL,
  verification_status text NOT NULL CHECK (verification_status IN ('VERIFIED', 'UNVERIFIED', 'REJECTED')),
  verification_method text NOT NULL CHECK (verification_method IN ('OFFICIAL', 'MAP_POI', 'ROUTE_ENDPOINT_VERIFIED', 'CURATED')),
  source_refs jsonb NOT NULL,
  valid_from date,
  valid_to date,
  PRIMARY KEY (publication_id, fact_id)
);
CREATE INDEX place_entrance_position_gix ON travel_projection.place_entrance USING gist(position);

CREATE TABLE travel_projection.amenity_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  source_id text NOT NULL,
  fact_id text NOT NULL,
  place_fact_id text,
  amenity_type text NOT NULL,
  availability text NOT NULL,
  guarantee text NOT NULL,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.bus_stop_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  source_id text NOT NULL,
  fact_id text NOT NULL,
  source_record_id text NOT NULL,
  provider_stop_id text NOT NULL,
  name text NOT NULL,
  direction_text text,
  position geography(Point, 4326) NOT NULL,
  data_as_of date NOT NULL,
  observed_at timestamptz NOT NULL,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (publication_id, fact_id)
);
CREATE INDEX bus_stop_position_gix ON travel_projection.bus_stop_fact USING gist(position);

CREATE TABLE travel_projection.bus_route_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  source_id text NOT NULL,
  fact_id text NOT NULL,
  provider_route_id text NOT NULL,
  route_number text NOT NULL,
  origin_name text,
  destination_name text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.bus_route_stop (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  route_fact_id text NOT NULL,
  stop_fact_id text NOT NULL,
  route_sequence integer NOT NULL CHECK (route_sequence > 0),
  direction_text text,
  PRIMARY KEY (publication_id, route_fact_id, route_sequence),
  UNIQUE (publication_id, route_fact_id, stop_fact_id, route_sequence)
);

CREATE TABLE travel_projection.service_calendar (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  service_id text NOT NULL,
  day_type text NOT NULL CHECK (day_type IN ('WEEKDAY', 'SATURDAY', 'SUNDAY', 'HOLIDAY')),
  starts_on date NOT NULL,
  ends_on date NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  CHECK (ends_on >= starts_on)
);

CREATE TABLE travel_projection.scheduled_trip (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  trip_id text NOT NULL,
  route_fact_id text NOT NULL,
  service_id text NOT NULL,
  direction_text text,
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.scheduled_stop_time (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  trip_id text NOT NULL,
  stop_fact_id text NOT NULL,
  stop_sequence integer NOT NULL CHECK (stop_sequence > 0),
  arrival_at time NOT NULL,
  departure_at time NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, trip_id, stop_sequence),
  CHECK (departure_at >= arrival_at)
);

CREATE TABLE travel_projection.stop_identity_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  canonical_stop_id text NOT NULL,
  provider text NOT NULL,
  provider_stop_id text NOT NULL,
  provider_route_id text,
  source_fact_id text NOT NULL,
  latitude double precision NOT NULL,
  longitude double precision NOT NULL,
  normalized_name text NOT NULL,
  direction_text text,
  route_sequence integer,
  mapping_method text NOT NULL CHECK (mapping_method IN ('OFFICIAL_ID', 'COORDINATE_AND_NAME', 'ROUTE_SEQUENCE', 'CURATED')),
  mapping_confidence double precision NOT NULL CHECK (mapping_confidence BETWEEN 0 AND 1),
  mapping_status text NOT NULL CHECK (mapping_status IN ('CONFIRMED', 'REVIEW_REQUIRED', 'REJECTED')),
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.route_identity_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  canonical_route_id text NOT NULL,
  provider text NOT NULL,
  provider_route_id text NOT NULL,
  mapping_status text NOT NULL CHECK (mapping_status IN ('CONFIRMED', 'REVIEW_REQUIRED', 'REJECTED')),
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.restroom_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  source_id text NOT NULL,
  fact_id text NOT NULL,
  name text NOT NULL,
  source_address text NOT NULL,
  geocoded_position geography(Point, 4326),
  geocoding_source_ref text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (publication_id, fact_id)
);

CREATE TABLE travel_projection.source_coverage (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  capability text NOT NULL,
  coverage_ratio double precision NOT NULL CHECK (coverage_ratio BETWEEN 0 AND 1),
  reason_code text,
  PRIMARY KEY (publication_id, capability)
);

CREATE FUNCTION travel_projection.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'IMMUTABLE_PROJECTION:%', TG_TABLE_NAME;
END
$$;

DO $$
DECLARE projection_table text;
BEGIN
  FOREACH projection_table IN ARRAY ARRAY[
    'place_fact', 'place_opening_period', 'place_closed_date', 'place_entrance',
    'amenity_fact', 'bus_stop_fact', 'bus_route_fact', 'bus_route_stop',
    'service_calendar', 'scheduled_trip', 'scheduled_stop_time', 'stop_identity_fact',
    'route_identity_fact', 'restroom_fact', 'source_coverage'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER %I_immutable BEFORE UPDATE OR DELETE ON travel_projection.%I '
      'FOR EACH ROW EXECUTE FUNCTION travel_projection.reject_mutation()',
      projection_table, projection_table
    );
  END LOOP;
END
$$;

CREATE VIEW travel_read.active_source_metadata AS
SELECT ds.source_id, ds.provider, ds.source_name, p.publication_id, p.dataset_version,
       p.source_date, p.observed_at, p.published_at, active.activated_at
FROM source_admin.active_snapshot active
JOIN source_admin.publication p ON p.publication_id = active.publication_id
JOIN source_admin.data_source ds ON ds.source_id = active.source_id;

CREATE VIEW travel_read.active_place AS
SELECT place.* FROM travel_projection.place_fact place
JOIN source_admin.active_snapshot active
  ON active.source_id = place.source_id AND active.publication_id = place.publication_id;

CREATE VIEW travel_read.active_place_hours AS
SELECT hours.* FROM travel_projection.place_opening_period hours
JOIN source_admin.active_snapshot active ON active.publication_id = hours.publication_id;

CREATE VIEW travel_read.active_place_entrance AS
SELECT entrance.* FROM travel_projection.place_entrance entrance
JOIN source_admin.active_snapshot active ON active.publication_id = entrance.publication_id;

CREATE VIEW travel_read.active_amenity AS
SELECT amenity.* FROM travel_projection.amenity_fact amenity
JOIN source_admin.active_snapshot active
  ON active.source_id = amenity.source_id AND active.publication_id = amenity.publication_id;

CREATE VIEW travel_read.active_bus_stop AS
SELECT stop.* FROM travel_projection.bus_stop_fact stop
JOIN source_admin.active_snapshot active
  ON active.source_id = stop.source_id AND active.publication_id = stop.publication_id;

CREATE VIEW travel_read.active_bus_route AS
SELECT route.* FROM travel_projection.bus_route_fact route
JOIN source_admin.active_snapshot active
  ON active.source_id = route.source_id AND active.publication_id = route.publication_id;

CREATE VIEW travel_read.active_bus_route_stop AS
SELECT value.* FROM travel_projection.bus_route_stop value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_service_calendar AS
SELECT value.* FROM travel_projection.service_calendar value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_scheduled_trip AS
SELECT value.* FROM travel_projection.scheduled_trip value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_stop_time AS
SELECT value.* FROM travel_projection.scheduled_stop_time value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.active_stop_identity AS
SELECT value.* FROM travel_projection.stop_identity_fact value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE VIEW travel_read.source_status AS
SELECT ds.source_id, ds.source_name, contract.license_status,
       metadata.dataset_version, metadata.published_at
FROM source_admin.data_source ds
LEFT JOIN LATERAL (
  SELECT sc.license_status FROM source_admin.source_contract sc
  WHERE sc.source_id = ds.source_id ORDER BY sc.registered_at DESC LIMIT 1
) contract ON true
LEFT JOIN travel_read.active_source_metadata metadata ON metadata.source_id = ds.source_id;

CREATE VIEW travel_read.acquisition_audit AS
SELECT source_id, status, source_date, observed_at, collected_at, raw_row_count,
       accepted_row_count, rejected_row_count, normalization_schema_version
FROM source_admin.acquisition;

REVOKE ALL ON SCHEMA source_admin, travel_projection, travel_read FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA source_admin, travel_projection, travel_read FROM PUBLIC;
GRANT USAGE ON SCHEMA source_admin, travel_projection TO jeju_importer;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA source_admin TO jeju_importer;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA travel_projection TO jeju_importer;
REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA source_admin, travel_projection FROM jeju_importer;
GRANT USAGE ON SCHEMA travel_read TO jeju_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA travel_read TO jeju_runtime;
