-- 원본·publication·projection은 계속 append-only로 두고 상태 포인터만 전환한다.
GRANT UPDATE (finished_at, status)
ON source_admin.refresh_run TO jeju_importer;

GRANT UPDATE (status, reason_code)
ON source_admin.refresh_run_item TO jeju_importer;

GRANT UPDATE (
  status,
  raw_row_count,
  accepted_row_count,
  rejected_row_count,
  normalized_checksum,
  validated_at
)
ON source_admin.acquisition TO jeju_importer;

GRANT UPDATE (publication_id, activated_at)
ON source_admin.active_snapshot TO jeju_importer;

REVOKE UPDATE, DELETE ON source_admin.raw_object FROM jeju_importer;
REVOKE UPDATE, DELETE ON source_admin.publication FROM jeju_importer;
REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA travel_projection FROM jeju_importer;
