# 2026-08-30 2차 안전 공백 보완

## 상태

코드·공개 계약·로컬 active DB fail-closed 판정은 `Pass`다. 외부 API key와 최신 source
publication이 필요한 종단 성공 인수는 이번 변경 범위 밖이므로 `Deferred`다.

## 해결한 공백

1. capability의 `false`를 `STALE`, `COVERAGE_INCOMPLETE`, `BLOCKED`, `MISSING`,
   `SOURCE_NOT_APPROVED`로 구분해 보존한다. 요청 단위 exact 버스 probe는 fresh publication의
   coverage 미완료일 때만 허용하며 stale 시간표를 다시 열지 않는다.
2. 장소 검색은 장소·제주 경계 freshness를 먼저 확인하고, stale이면 빈 정상 결과 대신
   `PLACE_SEARCH_SOURCE_STALE`을 반환한다. 정류장 조회도 정류장·identity source freshness를
   통과해야 한다.
3. 생성뿐 아니라 Evaluate·Revalidate·transfer preview도 여행일과 허용 수단의 readiness를
   모두 통과하기 전에는 runtime route/evidence adapter를 호출하지 않는다. 일부 수단만
   준비된 다중 수단 요청도 stale 수단을 시도할 수 없도록 전체를 fail-closed한다.
4. 버스와 택시 요금 readiness를 `bus_fare_policy_ready`와
   `taxi_fare_policy_ready`로 분리했다. 기존 단일 coverage는 source ID별로 호환 해석한다.
5. 생성·판정·이동 미리보기의 모든 `SourceRef.source_id`는 같은 응답의 `data_sources` ledger에
   닫혀야 한다. 검색·정류장 조회도 active-view 별칭 대신 승인된 canonical source ID를
   반환한다.

## 실제 active DB 판정

2026-08-30 로컬 active DB를 새 원인 보존 gate로 조회한 결과는 다음과 같다.

- `place_search_ready=STALE`, `service_area_ready=STALE`
- `bus_fare_policy_ready=READY`, `taxi_fare_policy_ready=STALE`
- `future_bus_planning_ready=COVERAGE_INCOMPLETE`
- `opening_hours_ready=COVERAGE_INCOMPLETE`
- 장소 검색: `data_unavailable / PLACE_SEARCH_SOURCE_STALE`
- 존재하지 않는 정류장 조회: fresh 정류장·mapping source를 통과한 뒤 `not_found`

따라서 오래된 장소·경계·택시 정책이나 불완전한 시간표 coverage로 성공을 가장하지 않으며,
fresh한 버스 요금은 stale 택시 정책과 독립적으로 유지된다.

## 검증 근거

- 한글 테스트 목적 설명: Pass
- Ruff: Pass
- Pyright: Pass
- offline pytest: 468 passed, 9 live-environment skipped
- 격리 PostGIS·MinIO integration: 24 passed, 0 skipped
- Pydantic JSON Schema 동기화: Pass

## 검증 공백과 잔여 위험

- 현재 실행 환경에 외부 API key가 없어 최신 공식 원본 재수집과 실제 TMAP/TAGO 종단 성공
  인수는 수행하지 않았다.
- 장소·경계·택시 요금 source를 최신 publication으로 교체하고 전역 운영시간·버스 시간표
  coverage를 완성하기 전까지 전체 추천 availability는 승인하지 않는다.
- GitHub required status check는 저장소 요금제·설정 권한에 따라 강제되지 않을 수 있으므로
  PR과 main CI 결과를 계속 직접 확인한다.
