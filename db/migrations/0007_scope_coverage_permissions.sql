-- exact-scope capability coverage의 분모 조회를 importer에 최소 권한으로 허용한다.

GRANT USAGE ON SCHEMA travel_read TO jeju_importer;
GRANT SELECT ON travel_read.active_service_scope_member TO jeju_importer;
