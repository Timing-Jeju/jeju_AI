-- 공식·수동 검증 메뉴의 명시적 식이 안전성 주장만 append-only fact로 보존한다.

CREATE TABLE travel_projection.restaurant_dietary_fact (
  publication_id uuid NOT NULL REFERENCES source_admin.publication(publication_id),
  fact_id text NOT NULL,
  place_fact_id text NOT NULL,
  menu_item_id text NOT NULL,
  menu_item_name text NOT NULL,
  verified_free_from_allergens text[] NOT NULL DEFAULT ARRAY[]::text[],
  verified_excludes_foods text[] NOT NULL DEFAULT ARRAY[]::text[],
  verification_method text NOT NULL CHECK (verification_method IN ('OFFICIAL', 'CURATED')),
  source_refs jsonb NOT NULL,
  valid_from date NOT NULL,
  valid_to date NOT NULL,
  last_verified_at timestamptz NOT NULL,
  verification_expires_at timestamptz NOT NULL,
  PRIMARY KEY (publication_id, fact_id),
  UNIQUE (publication_id, place_fact_id, menu_item_id),
  CHECK (
    cardinality(verified_free_from_allergens) + cardinality(verified_excludes_foods) > 0
  ),
  CHECK (valid_to >= valid_from),
  CHECK (verification_expires_at > last_verified_at)
);

CREATE INDEX restaurant_dietary_place_idx
ON travel_projection.restaurant_dietary_fact (place_fact_id);

CREATE TRIGGER restaurant_dietary_fact_immutable
BEFORE UPDATE OR DELETE ON travel_projection.restaurant_dietary_fact
FOR EACH ROW EXECUTE FUNCTION travel_projection.reject_mutation();

CREATE VIEW travel_read.active_restaurant_dietary_fact AS
SELECT value.* FROM travel_projection.restaurant_dietary_fact value
JOIN source_admin.active_snapshot active ON active.publication_id = value.publication_id;

GRANT SELECT, INSERT ON travel_projection.restaurant_dietary_fact TO jeju_importer;
REVOKE UPDATE, DELETE ON travel_projection.restaurant_dietary_fact FROM jeju_importer;

GRANT SELECT ON travel_read.active_restaurant_dietary_fact TO jeju_runtime;
