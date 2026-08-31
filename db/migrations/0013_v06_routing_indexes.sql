-- v0.6 전역 후보·복수 정류장·직통/1회 환승 조회를 위한 append-only 보조 인덱스.

CREATE INDEX bus_route_stop_lookup_idx
  ON travel_projection.bus_route_stop
  (stop_fact_id, direction_text, route_fact_id, route_sequence);

CREATE INDEX bus_route_stop_route_sequence_idx
  ON travel_projection.bus_route_stop
  (route_fact_id, direction_text, route_sequence, stop_fact_id);

CREATE INDEX scheduled_trip_service_lookup_idx
  ON travel_projection.scheduled_trip
  (route_fact_id, service_id, direction_text,
   timetable_effective_from, timetable_effective_to, trip_id);

CREATE INDEX scheduled_stop_time_trip_stop_idx
  ON travel_projection.scheduled_stop_time
  (trip_id, stop_fact_id, stop_sequence, departure_at);

CREATE INDEX stop_identity_source_status_idx
  ON travel_projection.stop_identity_fact
  (source_fact_id, mapping_status, canonical_stop_id);

CREATE INDEX service_calendar_date_lookup_idx
  ON travel_projection.service_calendar
  (service_id, day_type, starts_on, ends_on);
