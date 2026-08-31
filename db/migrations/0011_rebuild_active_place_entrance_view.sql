-- 0003에서 입구 검증·지원수단 열을 추가하기 전에 생성된 view의 고정 열 목록을 갱신한다.
DROP VIEW travel_read.active_place_entrance;

CREATE VIEW travel_read.active_place_entrance AS
SELECT
  entrance.publication_id,
  entrance.fact_id,
  entrance.place_fact_id,
  entrance.entrance_id,
  entrance.entrance_type,
  entrance.position,
  entrance.verification_status,
  entrance.verification_method,
  entrance.source_refs,
  entrance.valid_from,
  entrance.valid_to,
  entrance.last_verified_at,
  entrance.verification_expires_at,
  entrance.supported_modes
FROM travel_projection.place_entrance entrance
JOIN source_admin.active_snapshot active
  ON active.publication_id = entrance.publication_id;

GRANT SELECT ON travel_read.active_place_entrance TO jeju_runtime;
