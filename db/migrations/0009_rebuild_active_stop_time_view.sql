-- 0003에서 stop-time offset 열을 추가하기 전에 생성된 view의 고정 열 목록을 갱신한다.
DROP VIEW travel_read.active_stop_time;

CREATE VIEW travel_read.active_stop_time AS
SELECT
  value.publication_id,
  value.fact_id,
  value.trip_id,
  value.stop_fact_id,
  value.stop_sequence,
  value.arrival_at,
  value.departure_at,
  value.arrival_day_offset,
  value.departure_day_offset
FROM travel_projection.scheduled_stop_time value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT ON travel_read.active_stop_time TO jeju_runtime;
