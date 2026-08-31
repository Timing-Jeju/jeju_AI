-- 경유정류장 catalog coverage를 canonical stop identity 근거로 오인하지 않는다.
DROP VIEW travel_read.active_source_coverage;

CREATE VIEW travel_read.active_source_coverage AS
SELECT
  coverage.publication_id,
  coverage.capability,
  coverage.coverage_ratio,
  coverage.reason_code,
  coverage.region_code,
  coverage.grid_id,
  coverage.service_date_from,
  coverage.service_date_to,
  coverage.measured_at,
  coverage.blocking_reason
FROM travel_projection.source_coverage coverage
JOIN source_admin.active_snapshot active
  ON active.publication_id = coverage.publication_id
JOIN source_admin.publication publication
  ON publication.publication_id = coverage.publication_id
WHERE NOT (
  coverage.capability = 'confirmed_stop_mapping_ready'
  AND publication.source_id <> 'transport.stop-identity-map'
);

GRANT SELECT ON travel_read.active_source_coverage TO jeju_runtime;
