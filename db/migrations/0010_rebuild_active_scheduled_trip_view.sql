-- 0003에서 시간표 시행일 열을 추가하기 전에 생성된 trip view의 고정 열 목록을 갱신한다.
DROP VIEW travel_read.active_scheduled_trip;

CREATE VIEW travel_read.active_scheduled_trip AS
SELECT
  value.publication_id,
  value.fact_id,
  value.trip_id,
  value.route_fact_id,
  value.service_id,
  value.direction_text,
  value.timetable_effective_from,
  value.timetable_effective_to
FROM travel_projection.scheduled_trip value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT ON travel_read.active_scheduled_trip TO jeju_runtime;
