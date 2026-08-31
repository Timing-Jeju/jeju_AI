-- 실제 데이터 적재 전 제주 서비스영역과 coverage 측정 근거를 보강한다.

CREATE TABLE travel_projection.service_area_boundary (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  boundary_id text NOT NULL,
  name text NOT NULL,
  geometry geometry(MultiPolygon, 4326) NOT NULL,
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, boundary_id),
  CHECK (ST_IsValid(geometry)),
  CHECK (NOT ST_IsEmpty(geometry))
);

CREATE TRIGGER service_area_boundary_immutable
BEFORE UPDATE OR DELETE ON travel_projection.service_area_boundary
FOR EACH ROW EXECUTE FUNCTION travel_projection.reject_mutation();

CREATE VIEW travel_read.active_service_area_boundary AS
SELECT value.* FROM travel_projection.service_area_boundary value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

CREATE TABLE travel_projection.bus_fare_policy_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  fare_class text NOT NULL CHECK (fare_class IN ('STANDARD', 'EXPRESS')),
  effective_from date NOT NULL,
  effective_to date,
  adult_min_krw integer NOT NULL CHECK (adult_min_krw >= 0),
  adult_max_krw integer NOT NULL CHECK (adult_max_krw >= adult_min_krw),
  child_min_krw integer NOT NULL CHECK (child_min_krw >= 0),
  child_max_krw integer NOT NULL CHECK (child_max_krw >= child_min_krw),
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TRIGGER bus_fare_policy_fact_immutable
BEFORE UPDATE OR DELETE ON travel_projection.bus_fare_policy_fact
FOR EACH ROW EXECUTE FUNCTION travel_projection.reject_mutation();

CREATE VIEW travel_read.active_bus_fare_policy AS
SELECT value.* FROM travel_projection.bus_fare_policy_fact value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT, INSERT ON travel_projection.service_area_boundary,
  travel_projection.bus_fare_policy_fact TO jeju_importer;
REVOKE UPDATE, DELETE ON travel_projection.service_area_boundary,
  travel_projection.bus_fare_policy_fact FROM jeju_importer;
GRANT SELECT ON travel_read.active_service_area_boundary,
  travel_read.active_bus_fare_policy TO jeju_runtime;
