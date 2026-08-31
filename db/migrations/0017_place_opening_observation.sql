-- 완료된 상세소개 조회 결과를 정확 운영시간과 분리해 장소별 append-only 상태로 보존한다.

CREATE TABLE travel_projection.place_opening_observation (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  observation_status text NOT NULL CHECK (
    observation_status IN ('STRUCTURED', 'PARTIAL', 'UNVERIFIABLE', 'NO_DATA')
  ),
  reason_code text,
  source_refs jsonb NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, place_fact_id),
  CHECK (
    (observation_status = 'STRUCTURED' AND reason_code IS NULL)
    OR (observation_status <> 'STRUCTURED' AND reason_code IS NOT NULL)
  )
);

CREATE TRIGGER place_opening_observation_immutable
BEFORE UPDATE OR DELETE ON travel_projection.place_opening_observation
FOR EACH ROW EXECUTE FUNCTION travel_projection.reject_mutation();

CREATE VIEW travel_read.active_place_opening_observation AS
SELECT value.* FROM travel_projection.place_opening_observation value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT, INSERT ON travel_projection.place_opening_observation TO jeju_importer;
REVOKE UPDATE, DELETE ON travel_projection.place_opening_observation FROM jeju_importer;

GRANT SELECT ON travel_read.active_place_opening_observation TO jeju_runtime;
