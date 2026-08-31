# v0.6 통합 최종 인수 결과 (2026-08-18)

## 결론

`Phase 1 Pass / same-day TAGO deferred`다. v0.6 공개 계약, 제주 전역 동적 후보, 다일 이력,
직통·1회 환승 버스, Generate→Evaluate→Revalidate 타임라인 보존, 시간·근거 상태 분리와
fail-closed 규칙은 구현·offline 회귀 검증을 마쳤다. 도보·택시 라이브 요청은 동일 타임라인으로
판정과 재검증까지 통과했고, 요청 범위 exact 시간표가 확인된 제주알호텔 bus-only 요청은
서로 다른 세 추천을 실제로 생성했다.

제주 전역 공식 평일 시간표와 활성 TAGO route pattern의 exact 연결 자체는 여전히
369/974(37.8850%)다. 따라서 전역 `future_bus_planning_ready=1.0`은 발행하지 않는다. 대신 요청의
숙소·필수·선호 장소마다 당일 confirmed stop-time이 존재하는 경우에만 request-scoped gate를
열고, 최종 생성기가 모든 구간을 다시 검증한다. 오늘 외부 API 사용량이 80%에 도달해 사용자가
추가 호출 중단을 요청했으므로, 마지막 환승 판정 수정 뒤의 live Evaluate·same-day TAGO
Revalidate 재실행만 다음 가용 창으로 미뤘다.

## 통합 변경

- 공식 workbook sheet 제목의 방향 waypoint를 노선 순서대로 대조한다.
- checkpoint 수와 시작·종점이 동률인 후보 중 제목 waypoint가 두 개 이상 일치하고 최고 점수가
  유일할 때만 exact route pattern으로 확정한다.
- 제목 일치까지 동률이거나 시행일·정류장 시각이 부족한 행은 기존 reason code와 함께 제외한다.
- 기존 publication의 trip·stop-time evidence ID를 유지하고 새 source lineage에만 결정적 ID를
  부여한다.
- 버스-only는 숙소 주변 exact 운행편에서 장소 조합을 역탐색하고 전략별 primary·fallback을
  만든 뒤, 실제 TMAP 접근·환승·하차 도보를 최대 20회 안에서 검증한다.
- 정류장 후보 하나의 TMAP 실패가 다른 정류장 쌍을 중단하지 않으며, 같은 정류장 환승은 0분
  연속 이동으로 처리하고 같은 공개 노선번호를 가짜 환승으로 만들지 않는다.
- Evaluate는 버스 총거리와 1회 환승의 첫 승차·최종 하차 claim을 손실 없이 대조한다.
- Revalidate는 운영시간 근거 부족을 시간 위험으로 바꾸지 않고 `timing_status`와
  `evidence_status`를 독립적으로 유지한다.

공식 group metadata에는 `GSCHEDULE_ID`, `SCHEDULE_ID`, `SHEET_NM`, `SHEET_NUM`,
`TOTAL_CNT`만 존재해 TAGO provider route ID와의 직접 연결을 제공하지 않는다. 따라서 schedule
ID만으로 남은 2,427개 동률 pattern을 확정하는 경로는 추가하지 않았다.

## 공식 시간표 감사

2026-08-18 평일 기준 전체 workbook 234개, sheet 440개를 pinned checksum으로 다시 읽었다.

| 항목 | 결과 |
|---|---:|
| 평일 후보 행 | 5,723 |
| exact trip 행 | 2,751 |
| exact trip 비율 | 48.069194% |
| exact 노선번호 | 189 |
| exact route pattern | 337 |
| 비평일 제외 행 | 508 |

남은 차단 사유는 다음과 같다.

| reason code | 건수 |
|---|---:|
| `TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE` | 2,427 |
| `TIMETABLE_ROUTE_NUMBER_MISSING_FROM_TAGO_CATALOG` | 162 |
| `TIMETABLE_EFFECTIVE_DATE_MISSING` | 149 |
| `TIMETABLE_MAJOR_STOP_TIMES_INSUFFICIENT` | 133 |
| `TIMETABLE_MULTIPLE_CLOCKS_PER_CELL_UNSUPPORTED` | 70 |
| `TIMETABLE_DEMAND_RESPONSIVE_ROW_UNSUPPORTED` | 31 |

활성 TAGO 범위는 247개 노선번호와 974개 route pattern이다. workbook의 노선번호 후보 범위는
245/247, route pattern 후보 범위는 972/974지만, 동일 노선번호의 복수 pattern을 포함하므로 이
후보 범위를 exact 시간표 coverage로 사용하지 않는다.

## 활성 publication과 evidence 연속성

| source | publication | dataset |
|---|---|---|
| `jeju.bus-timetable` | `d671a096-48bd-4ec7-876d-5b8d69e2e9fc` | `2026-08-18-a5f91321196f` |

| projection | active rows |
|---|---:|
| route-stop | 18,713 |
| scheduled trip | 2,931 |
| scheduled stop-time | 16,896 |
| exact route pattern | 369 |

- 직전 정상 publication: `76f338a7-42ed-443c-861c-81ee402ba2a6`
- 기존 trip 누락: 0
- 기존 stop-time 누락: 0
- 신규 trip: 160
- `JEJU_EAST/POC_V1 future_bus_planning_ready`: 1.0 발행
- `JEJU_ALL/ALL future_bus_planning_ready`: 369/974 = 0.378850, 미발행

새 증분 raw SHA-256은
`0464ea310e6ba87191a065eade6e72bc9e1c0237db348396ddcc1170d6df0407`이다. raw와 공식
workbook은 Git ignore된 owner-only 경로에 mode `0600`으로 보관하며 저장소에는 포함하지 않는다.

## 라이브 인수

기존 도보·택시 요청은 플레이스 캠프 제주 09:00~19:00, 새 bus-only 요청은 제주알호텔
07:00~19:00를 사용했다. 둘 다 2026-08-18 당일 요청이다.

| 시나리오 | 결과 |
|---|---|
| 표준 전역 bus-only | 추천 0, `DATA_NOT_READY`, missing=`future_bus_planning_ready` |
| 동부 readiness bus-only | 추천 0, `ROUTE_EVIDENCE_MISSING`, 택시 혼입 0 |
| 전역 도보·택시 Generate | `success`, relaxed·balanced·experience_max 세 추천 |
| 숙소 복귀 | 17:43, 17:13, 18:40로 모두 19:00 이전 |
| balanced Evaluate | `feasible_with_caution`, timing=`at_risk`, evidence=`partial`, window fit=true |
| balanced Revalidate | status/timing=`at_risk`, evidence=`partial`, 원본 15개 이벤트 보존 |
| 제주알호텔 request-scoped bus-only Generate | `success`, 서로 다른 추천 3개, 택시 혼입 0 |
| bus-only balanced | 버스 6구간, 18:11 숙소 복귀 |
| bus-only experience_max | 버스 6구간, 18:11 숙소 복귀, 정책 체류시간 구성이 balanced와 다름 |
| bus-only relaxed | 버스 5구간, 15:33 숙소 복귀, 관광지 수가 다른 일정 |
| bus-only TMAP 호출 | 총 18회, `bus_walk` 20회·전체 40회 제한 이내 |
| 수정 뒤 live Evaluate·TAGO | API 80% 사용 시점의 사용자 중단 요청으로 재실행 보류 |

Evaluate와 Revalidate의 `partial`은 운영시간·입구 등 일부 근거 부족을 나타내며, 시간 계산과
여행 창 적합 여부를 `unknown`이나 `data_unavailable`로 덮지 않는다.

마지막 live E2E 중 Evaluate에서 버스 총거리 누락과 1회 환승 최종 하차 비교 오류를 발견했다.
두 결함은 각각 RED 회귀 테스트를 추가한 뒤 수정했다. 관련 Generate→Evaluate→Revalidate,
버스-only 전 구간, 실제 활동 시작시각, 운영시간 unknown 분리 시나리오는 offline에서 통과했다.
외부 API를 다시 호출하지 않았으므로 이 수정 이후의 live 결과를 이 문서에서 성공으로 주장하지
않는다.

## 최신 품질 게이트

- 한글 테스트 목적 검사: Pass
- Ruff: Pass
- Pyright: 0 errors
- offline pytest: 390 passed, 9 skipped
- Pydantic Schema drift: Pass
- v0.6 synthetic example drift: Pass
- checksum manifest: Pass
- live TMAP bus-only Generate: Pass
- 수정 뒤 live Evaluate·same-day TAGO Revalidate: 사용자 사용량 보호 요청으로 Deferred

## 보호 조건

- 승인 source 목록은 `config/data_sources.toml`을 변경하지 않았다.
- TMAP raw body·geometry·사용자 원문·GPS 이력은 저장하거나 출력하지 않았다.
- 시간·거리·비용은 route/evidence fact와 정책 계산 결과만 사용한다.
- 외부 경로 호출 예산 40회와 동시성 4개를 완화하지 않았다.
- 외부 호출 개수는 그대로 두고 Evaluate·Revalidate의 벽시계 제한만 각각 90초로 늘렸다.
- 서로 다른 유효 추천 세 개가 없으면 `insufficient_feasible_routes`만 반환한다.
