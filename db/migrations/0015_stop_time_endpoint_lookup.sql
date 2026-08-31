-- 정류장 후보에서 exact trip을 역으로 찾는 직통·환승 조회를 위한 보조 인덱스.

CREATE INDEX scheduled_stop_time_stop_trip_sequence_idx
  ON travel_projection.scheduled_stop_time
  (stop_fact_id, trip_id, stop_sequence, departure_at, arrival_at);
