# 1차 공백 최종 복구·라이브 재인수 (2026-08-30)

## 결론

상태: `Phase 1 Core Pass / same-day selected-bus TAGO pending service date`

현재 `main` 코드와 승인된 외부 키, active Postgres publication, private MinIO 원본을 함께
점검했다. 오늘 운행 가능한 도보·택시는 Generate→Evaluate→Revalidate 전 과정을 통과했고,
2026-08-31 공식 평일 시간표를 새로 raw-first 발행한 뒤 버스 전용 Generate→Evaluate와
정적 Revalidate도 통과했다. 성공 생성은 두 경우 모두 서로 다른 추천 정확히 세 개였으며,
버스 일정에는 택시나 직접 도보 transfer가 섞이지 않았다.

8월 31일 선택 버스의 TAGO 재검증은 운행일과 실시간 관측일이 같아야 하므로 8월 30일에는
실행할 수 없다. 오늘 TAGO endpoint와 정류장 mapping의 live smoke는 통과했지만, 그 관측값을
내일 일정에 붙이지 않았다. 이는 남은 코드·키·publication 결함이 아니라 외부 시간 조건이다.

## 공식 소스 재확인과 계약 수정

- 국가데이터처의 SGIS 제주 경계 최신 파일은 여전히 2025-06-30 기준이며, 다음 등록 예정일은
  2027-02-01이다. 경계 freshness를 이 공지일까지 맞추고 2026-08-30 재검토 이력을 남겼다.
- 제주버스정보시스템의 현행 택시 요율은 2024-07-01 시행 요율과 동일했다. 정책의 시행일은
  바꾸지 않고 `verified_on=2026-08-30`을 추가했다.
- 제주 공식 시간표의 `source_date`는 수집일이 아니라 운행일이다. 기존 공통 freshness가
  다음 날 운행표를 미래 데이터로 오판하던 문제를 RED로 재현하고, 이 소스에만 45일 미래
  허용 계약을 명시했다. 실제 coverage는 별도 발행된 정확한 운행일에만 열린다.
- 기본 미래 허용값 0은 기존 다른 소스의 contract fingerprint를 바꾸지 않도록 호환성을
  고정했다. 시간표와 SGIS 경계의 현재 fingerprint는 source-admin DB에도 등록됐고, 기존
  원본 checksum 재검증 결과는 `NO_CHANGE`여서 active projection을 임의로 교체하지 않았다.
- 메모리 전용 preflight 증거가 TAGO에도 TMAP 이름으로 표시되던 진단 문구를
  `MEMORY_ONLY_RETENTION`으로 바로잡았다.

## raw-first publication과 활성 상태

| 소스 | active publication | dataset |
|---|---|---|
| `tourapi.place` | `3d4d6b05-d808-4d5b-87b6-58ebe90bfa42` | `2026-08-30-6644938dd520` |
| `jeju.taxi-fare-policy` | `0bcfd340-fefb-4fdd-895f-ea1569e90c72` | `2026-08-30-8d0bacb4fbbd` |
| `jeju.bus-timetable` | `9be91464-d629-42a5-843e-696aa9f72d4a` | `2026-08-31-137a08b1f4ed` |
| `spatial.jeju-boundary` | `23e3c313-7e66-4421-95a7-adbdf1445c78` | `2025-06-30-75afd50c3957` |

8월 31일 시간표는 고정 checksum의 공식 workbook 234개와 8월 30일까지 검토한 버스 공지로
다시 만들었다. raw ZIP checksum은
`308f887cc3491e8d4152c57e2e578f35e8a5421394df622426be96cf264446c8`이며,
scheduled trip 2,931개, stop-time 16,896개를 active로 읽는다. 전체 exact pattern coverage는
`0.378850`이므로 `JEJU_ALL/ALL=1.0`을 발행하지 않았다. 검증된
`JEJU_EAST/POC_V1`과 `JEJU_R_HOTEL/BUS_CANDIDATE_V1`의 8월 31일 coverage만 각각 1.0으로
활성화했다.

현재 active 집계는 장소 992개, 버스 정류장 4,273개, confirmed mapping 4,273개다.
`service_area_ready`, `place_search_ready`, 버스·택시 요금, 노선·정류장 catalog와 mapping은
READY다. 전역 시간표·운영시간·scope는 실제 비율대로 `COVERAGE_INCOMPLETE`를 유지한다.

## 실제 서비스 경로 재인수

사용자 원문과 GPS를 넣거나 저장하지 않았고, 출력에는 TMAP geometry·원본 응답·좌표를
남기지 않았다.

### 2026-08-30 도보·택시

대표 숙소는 제주알(R)호텔, 활동창은 09:00~20:00이며, 자동 식사·카페를 끈 일반 발견
요청이다.

| 단계 | 결과 |
|---|---|
| Generate | `success`, balanced·relaxed·experience_max 정확히 3개 |
| Evaluate | `feasible_with_caution`, timing=`at_risk`, evidence=`verified` |
| Revalidate | `on_schedule`, evidence=`verified`, location=`event` |
| 외부 근거 | TMAP pedestrian/driving, TourAPI place, 공식 택시 요율, SGIS 경계 |

### 2026-08-31 버스 전용

대표 숙소는 제주알(R)호텔, 활동창은 07:00~19:00, 구간당 최대 환승 1회다. 전역 coverage가
미완료여도 요청 장소와 평가 timeline의 모든 endpoint 주변에 해당 운행일 exact 시간표와
confirmed 정류장이 있을 때만 request-scoped gate를 연다. stale 시간표는 이 probe로 다시
열 수 없다.

| 단계 | 결과 |
|---|---|
| Generate | `success`, 서로 다른 추천 정확히 3개 |
| 전략별 버스 transfer | 5 / 6 / 5 |
| 비버스 transfer | 0 / 0 / 0 |
| Evaluate | `feasible_with_caution`, timing=`at_risk`, evidence=`verified` |
| 정적 Revalidate | `at_risk`, evidence=`verified`, location=`event` |
| 선택 버스 당일 TAGO | 2026-08-31 운행일 도달 전이므로 미실행 |

평가 gate도 생성과 같은 exact endpoint 근거를 확인하도록 RED 후 보강했다. 따라서 전역
coverage 미완료라는 이유만으로 이미 생성된 exact 버스 timeline을 `unverifiable`로 오판하지
않으며, endpoint 근거가 없으면 계속 닫힌다.

## 저장·보안 점검

- 기존 `travle-jeju/.env`를 새 파일로 복제하지 않고 ignore된 symlink로 주입했다. 실제 target은
  mode `0600`이며 secret 값은 출력하지 않았다.
- private raw bucket의 versioning은 `Enabled`다.
- 시간표·SGIS·TourAPI 원본은 private raw에 먼저 등록하거나 기존 checksum으로 재검증했다.
- TMAP·TAGO 원본은 memory-only이며 raw body와 상세 geometry를 영속 저장하지 않았다.
- 공개 응답은 Pydantic 모델을 통과했고, 시간·거리·비용은 evidence fact ID로 닫혔다.
- 세 유효 추천을 만들지 못한 중간 실행은 추천 0개와 `insufficient_feasible_routes`로 닫혔다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 전체 offline pytest: `474 passed, 9 skipped`
- 격리 PostGIS·MinIO integration: `24 passed`
- 최종 v0.6 checksum manifest: `Pass`

## 남은 범위와 판정

1차 핵심 안전 경로의 코드·키·active publication 공백은 해결됐다. 다만 아래 항목은 전역
완료로 과장하지 않는다.

1. 전역 exact 버스 pattern은 37.8850%다. 현재는 검증된 두 scope와 request-scoped exact
   endpoint만 지원하며, 제주 전역 1.0은 추가 공식 시간표 mapping 작업이 필요하다.
2. 전역 운영시간·scope coverage는 미완료다. snapshot은 준비됐고 일반 요청은 미확인을
   경고로 반환하지만, 모든 장소가 verified open인 전역 서비스를 의미하지 않는다.
3. verified 입구가 없는 장소는 일반 요청에서 공식 장소 대표좌표를 사용한다. 이동보조나
   계단 회피가 필수인 요청은 verified entrance coverage가 생길 때까지 안전하게 거부한다.
4. 알레르기·제외음식 조건의 자동 식당 추천은 별도 검증 capability가 없어 계속 닫힌다.
5. 8월 31일 선택 버스와 TAGO snapshot을 결합한 당일 Revalidate만 외부 시각 조건으로 남는다.

따라서 “기본 동작과 대표 도보·택시·버스 경로가 안전하게 돌아가는가”에는 `예`로 판정한다.
“제주 전역의 모든 장소·운행일·접근성·식이 조건이 완전한가”에는 아직 `아니오`다.
