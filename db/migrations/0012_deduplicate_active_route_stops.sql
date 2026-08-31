-- 전역 TAGO 경유정류장을 우선하고 시간표 publication의 fallback 중복을 제거한다.
DROP VIEW travel_read.active_bus_route_stop;

CREATE VIEW travel_read.active_bus_route_stop AS
SELECT
  preferred.publication_id,
  preferred.route_fact_id,
  preferred.stop_fact_id,
  preferred.route_sequence,
  preferred.direction_text,
  preferred.source_refs,
  preferred.position
FROM (
  SELECT DISTINCT ON (
    value.route_fact_id,
    value.route_sequence,
    value.direction_text
  )
    value.publication_id,
    value.route_fact_id,
    value.stop_fact_id,
    value.route_sequence,
    value.direction_text,
    value.source_refs,
    value.position
  FROM travel_projection.bus_route_stop value
  JOIN source_admin.active_snapshot active
    ON active.publication_id = value.publication_id
  JOIN source_admin.publication publication
    ON publication.publication_id = value.publication_id
  ORDER BY
    value.route_fact_id,
    value.route_sequence,
    value.direction_text,
    (publication.source_id = 'tago.bus-route-stops') DESC,
    active.activated_at DESC,
    value.publication_id DESC
) AS preferred;

GRANT SELECT ON travel_read.active_bus_route_stop TO jeju_runtime;
