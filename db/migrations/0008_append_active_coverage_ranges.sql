-- Immutable active publication에 새 날짜 coverage를 UPDATE 없이 append할 수 있게 한다.
ALTER TABLE travel_projection.source_coverage
  DROP CONSTRAINT source_coverage_pkey,
  ADD COLUMN coverage_id bigint GENERATED ALWAYS AS IDENTITY,
  ADD PRIMARY KEY (coverage_id),
  ADD CONSTRAINT source_coverage_identity_unique
    UNIQUE NULLS NOT DISTINCT (
      publication_id, capability, region_code, grid_id,
      service_date_from, service_date_to
    );

