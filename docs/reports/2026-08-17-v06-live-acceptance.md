# 제주 전역·다일 플래너 v0.6 순차 인수 결과

실행일: 2026-08-17 (Asia/Seoul)

## 상태

상태: `Pass with data limitations`

v0.6 코드·계약·격리 저장소·승인 외부 API를 순서대로 검증했다. 제주 남부 숙소 기준 일반
요청은 서로 다른 추천 세 개를 생성했고 같은 타임라인을 Evaluate와 Revalidate까지 전달했다.
현재 날짜 이후의 제주 전역 정확 버스 publication은 준비되지 않아 버스 전용 라이브 요청은
택시를 섞지 않고 `DATA_NOT_READY`로 닫혔다.

## 구현 중 발견해 수정한 문제

1. 공개 계약과 패키지 버전 불일치
   - 최초 RED: `pyproject.toml`의 배포 버전이 `0.5.0`이라 v0.6 계약과 달랐다.
   - 최소 GREEN: 패키지·lock·TMAP staging cache namespace를 `0.6.0`으로 통일했다.
   - 회귀: `test_package_version_matches_public_v06_contract`.
2. 남부 동적 후보의 경로 호출예산 소진
   - 최초 라이브 결과: `ROUTING_BUDGET_EXHAUSTED`, 추천 0개.
   - 최초 RED: 전략별 primary 뼈대의 고유 taxi edge가 17개였다.
   - 최소 GREEN: 관광 후보를 중첩하고 공통 식사·휴식 edge를 재사용해 고유 edge를 10개
     이하로 제한했다. 전체 40회와 taxi 10회 상한은 변경하지 않았다.
   - 회귀: `test_dynamic_primary_skeletons_share_routes_within_taxi_allocation`.
3. 모든 장소 앞에 반복되던 무조건 20분 버퍼
   - 최초 RED: 이미 운영 중인 장소 앞에도 근거 없는 buffer가 생성됐다.
   - 최소 GREEN: 운영 시작 또는 식사 시간창을 실제로 기다릴 때만 buffer를 만든다. 숙소
     복귀 20분 정책 여유는 유지한다.
   - 회귀: `test_generator_does_not_insert_unconditional_buffer_before_open_places`.

## 검증 근거

### 승인 외부 API

- TMAP memory-only 대표 권역 smoke: `3/3 pass`
  - source: `tmap.driving`
  - safe log: 3건
  - raw body·geometry·route metrics 영속 저장: `false`
  - 검증 입구가 아닌 대표좌표 기반이므로 운영 capability 자동 활성화: `false`
- TAGO same-day smoke: `pass`
  - source: `tago.bus-arrival`
  - 제주버스터미널 정류장 응답 10건을 typed arrival로 정규화
  - snapshot TTL: 60초
  - raw body 영속 저장: `false`

### 격리 저장소

- test PostGIS·MinIO live integration: `9 passed`
- runtime 역할 격리, migration checksum, append-only raw 등록, atomic publication,
  rollback, 실제 MinIO checksum·version HEAD를 검증했다.
- 테스트 DB는 `jeju_trip_test`, 테스트 MinIO는 `127.0.0.1:59010`의 tmpfs 인스턴스만
  사용했다.

### 제주 남부 실제 생성 흐름

- 숙소: 제주신라호텔 active place fact
- 여행일: 2026-08-18
- 허용 수단: 도보·택시
- Generate: `success`, 추천 3개
- 전략: `relaxed`, `balanced`, `experience_max` 각각 1개
- 가장 늦은 숙소 복귀: 18:17, 요청 종료 19:00 이전
- 선택된 이동은 모두 검증된 TMAP taxi fact와 정책 호출 buffer를 사용했다.
- 같은 balanced 타임라인 연쇄 결과:
  - Evaluate: `feasible_with_caution`, `timing_status=at_risk`,
    `evidence_status=partial`, `schedule_window_fit=true`
  - Revalidate: `at_risk`, `delay_minutes=0`, `evidence_status=partial`
  - 원본 입력 18개 이벤트와 original evaluation 18개 이벤트 일치
  - remaining evaluation 생성 성공

### 권역별 일반 요청

같은 2026-08-18 도보·택시 조건으로 동부·서부·남부 숙소를 순차 검증했다.

| 권역 | 숙소 | 결과 | 추천 수 | 가장 늦은 복귀 |
|---|---|---:|---:|---:|
| 동부 | 플레이스 캠프 제주 | success | 3 | 18:36 |
| 서부 | 마레보 비치호텔 | success | 3 | 18:19 |
| 남부 | 제주신라호텔 | success | 3 | 18:17 |

각 성공은 `balanced`, `relaxed`, `experience_max`를 하나씩 포함하고 19:00 복귀를 지켰다.

### 버스 전용 fail-closed

- 2026-08-18 버스 전용 라이브 요청: `insufficient_feasible_routes`
- failure: `DATA_NOT_READY`
- missing capabilities:
  - `confirmed_stop_mapping_ready`
  - `fare_policy_ready`
  - `future_bus_planning_ready`
- 반환 추천 0개, 택시 이벤트 0개

## 검증 공백

- 현재 active 정확 버스 coverage는 과거 동부 PoC 날짜에 한정되어 있어 2026-08-18 제주
  전역 버스-only 성공 경로를 라이브로 만들 수 없다.
- 운영시간과 검증 입구 coverage가 전역 1.0이 아니므로 일반 라이브 결과의
  `evidence_status=partial`은 정상적인 fail-open 품질 표시다.
- TAGO same-day adapter 자체는 통과했지만, 현재 날짜의 exact bus-only 추천이 없으므로 선택된
  버스 구간에 TAGO snapshot을 결합한 Revalidate 라이브 검증은 아직 수행하지 못했다.

## 잔여 위험과 다음 순서

1. 현재·미래 날짜의 제주 전역 service calendar, trip, stop-time과 confirmed identity coverage를
   raw-first publication으로 발행한다.
2. 버스-only 전 구간 Generate를 성공시킨 뒤 선택된 현재 승차 정류장에만 TAGO를 결합한다.
3. 동부·서부·남부 1회 smoke는 통과했으므로 반복 표본에서 권역별 3추천 성공률과
   `ROUTING_BUDGET_EXHAUSTED` 빈도를 측정한다.
4. 운영시간·입구 coverage는 시간 산술과 분리해 점진적으로 높이고, 이동보조 요청은 verified
   coverage가 준비될 때까지 계속 차단한다.
