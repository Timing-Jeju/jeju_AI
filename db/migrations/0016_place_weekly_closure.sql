-- 공식 반복 휴무 요일을 날짜별 예외와 분리해 append-only fact로 보존한다.

CREATE TABLE travel_projection.place_weekly_closure (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  service_day smallint NOT NULL CHECK (service_day BETWEEN 1 AND 7),
  valid_from date,
  valid_to date,
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TRIGGER place_weekly_closure_immutable
BEFORE UPDATE OR DELETE ON travel_projection.place_weekly_closure
FOR EACH ROW EXECUTE FUNCTION travel_projection.reject_mutation();

CREATE VIEW travel_read.active_place_weekly_closure AS
SELECT value.* FROM travel_projection.place_weekly_closure value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT, INSERT ON travel_projection.place_weekly_closure TO jeju_importer;
REVOKE UPDATE, DELETE ON travel_projection.place_weekly_closure FROM jeju_importer;

GRANT SELECT ON travel_read.active_place_weekly_closure TO jeju_runtime;
