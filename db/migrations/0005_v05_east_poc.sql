-- v0.5 제주 동부 PoC scope, 가격, template과 staged publication 계약.

ALTER TABLE source_admin.acquisition
  DROP CONSTRAINT acquisition_status_check,
  ADD CONSTRAINT acquisition_status_check CHECK (status IN (
    'INCOMPLETE', 'ACQUIRED', 'PARSE_FAILED', 'QUALITY_FAILED', 'VALIDATED',
    'STAGED', 'NO_CHANGE', 'PUBLISHED', 'PUBLICATION_FAILED'
  ));

CREATE TABLE travel_projection.place_price_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  price_kind text NOT NULL CHECK (price_kind IN ('ADMISSION', 'MEAL', 'DRINK')),
  traveler_type text NOT NULL DEFAULT 'STANDARD_ADULT'
    CHECK (traveler_type = 'STANDARD_ADULT'),
  unit_count integer NOT NULL DEFAULT 1 CHECK (unit_count = 1),
  min_krw integer NOT NULL CHECK (min_krw >= 0),
  max_krw integer NOT NULL CHECK (max_krw >= min_krw),
  is_exact boolean NOT NULL,
  valid_from date NOT NULL,
  valid_to date,
  source_refs jsonb NOT NULL CHECK (jsonb_array_length(source_refs) > 0),
  PRIMARY KEY (publication_id, fact_id),
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE travel_projection.service_scope_member (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  region_code text NOT NULL,
  grid_id text NOT NULL,
  member_type text NOT NULL CHECK (
    member_type IN ('PLACE', 'ENTRANCE', 'STOP', 'ROUTE')
  ),
  member_id text NOT NULL,
  role text NOT NULL,
  required boolean NOT NULL DEFAULT true,
  source_refs jsonb NOT NULL CHECK (jsonb_array_length(source_refs) > 0),
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, region_code, grid_id, member_type, member_id, role)
);

CREATE TABLE travel_projection.itinerary_template_step (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  region_code text NOT NULL,
  grid_id text NOT NULL,
  strategy text NOT NULL CHECK (strategy IN ('balanced', 'relaxed', 'experience_max')),
  sequence integer NOT NULL CHECK (sequence > 0),
  activity_type text NOT NULL CHECK (activity_type IN ('visit', 'meal', 'rest')),
  candidate_group text NOT NULL,
  required boolean NOT NULL DEFAULT true,
  source_refs jsonb NOT NULL CHECK (jsonb_array_length(source_refs) > 0),
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, region_code, grid_id, strategy, sequence)
);

CREATE TABLE travel_projection.itinerary_template_candidate (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  region_code text NOT NULL,
  grid_id text NOT NULL,
  candidate_group text NOT NULL,
  place_fact_id text NOT NULL,
  priority integer NOT NULL CHECK (priority > 0),
  source_refs jsonb NOT NULL CHECK (jsonb_array_length(source_refs) > 0),
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, region_code, grid_id, candidate_group, priority)
);

ALTER TABLE travel_projection.bus_route_stop
  ADD COLUMN source_refs jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE travel_projection.service_calendar
  ADD COLUMN source_refs jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE travel_projection.scheduled_trip
  ADD COLUMN source_refs jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE travel_projection.scheduled_stop_time
  ADD COLUMN source_refs jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE travel_projection.stop_identity_fact
  ADD COLUMN source_refs jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE travel_projection.bus_route_stop
  ADD CONSTRAINT bus_route_stop_source_refs_nonempty CHECK (jsonb_array_length(source_refs) > 0);
ALTER TABLE travel_projection.service_calendar
  ADD CONSTRAINT service_calendar_source_refs_nonempty CHECK (jsonb_array_length(source_refs) > 0);
ALTER TABLE travel_projection.scheduled_trip
  ADD CONSTRAINT scheduled_trip_source_refs_nonempty CHECK (jsonb_array_length(source_refs) > 0);
ALTER TABLE travel_projection.scheduled_stop_time
  ADD CONSTRAINT scheduled_stop_time_source_refs_nonempty CHECK (jsonb_array_length(source_refs) > 0);
ALTER TABLE travel_projection.stop_identity_fact
  ADD CONSTRAINT stop_identity_source_refs_nonempty CHECK (jsonb_array_length(source_refs) > 0);

CREATE TRIGGER place_price_fact_immutable BEFORE UPDATE OR DELETE
ON travel_projection.place_price_fact FOR EACH ROW
EXECUTE FUNCTION travel_projection.reject_mutation();
CREATE TRIGGER service_scope_member_immutable BEFORE UPDATE OR DELETE
ON travel_projection.service_scope_member FOR EACH ROW
EXECUTE FUNCTION travel_projection.reject_mutation();
CREATE TRIGGER itinerary_template_step_immutable BEFORE UPDATE OR DELETE
ON travel_projection.itinerary_template_step FOR EACH ROW
EXECUTE FUNCTION travel_projection.reject_mutation();
CREATE TRIGGER itinerary_template_candidate_immutable BEFORE UPDATE OR DELETE
ON travel_projection.itinerary_template_candidate FOR EACH ROW
EXECUTE FUNCTION travel_projection.reject_mutation();

CREATE VIEW travel_read.active_place_price AS
SELECT value.* FROM travel_projection.place_price_fact value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;
CREATE VIEW travel_read.active_service_scope_member AS
SELECT value.* FROM travel_projection.service_scope_member value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;
CREATE VIEW travel_read.active_itinerary_template_step AS
SELECT value.* FROM travel_projection.itinerary_template_step value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;
CREATE VIEW travel_read.active_itinerary_template_candidate AS
SELECT value.* FROM travel_projection.itinerary_template_candidate value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT, INSERT ON travel_projection.place_price_fact,
  travel_projection.service_scope_member,
  travel_projection.itinerary_template_step,
  travel_projection.itinerary_template_candidate TO jeju_importer;
REVOKE UPDATE, DELETE ON travel_projection.place_price_fact,
  travel_projection.service_scope_member,
  travel_projection.itinerary_template_step,
  travel_projection.itinerary_template_candidate FROM jeju_importer;
GRANT SELECT ON travel_read.active_place_price,
  travel_read.active_service_scope_member,
  travel_read.active_itinerary_template_step,
  travel_read.active_itinerary_template_candidate TO jeju_runtime;
