-- TAGO 경유 정류장 원본 좌표를 route pattern lineage에 보존한다.

ALTER TABLE travel_projection.bus_route_stop
  ADD COLUMN position geography(Point, 4326);

CREATE INDEX bus_route_stop_position_gix
  ON travel_projection.bus_route_stop USING gist(position);
