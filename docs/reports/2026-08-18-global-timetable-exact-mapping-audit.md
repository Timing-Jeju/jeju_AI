# 제주 전역 공식 시간표 exact mapping 감사

감사일은 2026-08-18이며 목표 서비스일은 평일인 2026-08-18이다. 원본 234개 XLSX는
owner-only `tmp/private`에서만 읽고, 문서에는 workbook 원문이나 셀 값을 기록하지 않았다.

## 판정 규칙

- sheet 제목 또는 명시적 `노선번호` 열로 공식 노선번호를 확인한다.
- 평일 목표일에는 휴일·토요일 sheet를 제외한다.
- 한 셀에 시각이 둘 이상 있거나 `실시간 호출형`인 행은 exact 운행편으로 만들지 않는다.
- 시각이 있는 checkpoint 이름을 같은 노선번호의 TAGO 정류장에 순서대로 결합한다.
- 각 checkpoint는 다음 순서에서 일치 정류장이 정확히 하나일 때만 사용한다.
- 가장 많은 checkpoint가 결합된 route pattern이 하나뿐이고 정류장 시각이 두 개 이상일 때만
  exact mapping으로 인정한다.
- 동률 pattern은 provider route ID를 추정하지 않고 제외한다.
- 23시 이후 0시는 검증된 시각을 유지한 채 24시대 service-day offset으로 변환한다.

## 재현 명령

```bash
set -a
source .env
set +a
.venv/bin/python scripts/audit_global_timetable_mapping.py \
  --input-dir tmp/private/official/jeju-bus-timetable/2026-08-17/workbooks \
  --checksum-file tmp/private/official/jeju-bus-timetable/2026-08-17/workbooks.SHA256SUMS \
  --target-date 2026-08-18
```

전역 exact 조건이 충족되지 않으면 집계 JSON을 출력하고 종료 코드 2를 반환한다.

## 결과

| 항목 | 값 |
|---|---:|
| 평일 고유 후보 운행행 | 5,723 |
| exact mapping 운행행 | 2,406 |
| exact mapping 행 비율 | 42.04% |
| exact 노선번호 | 183 |
| exact route pattern | 281 |
| 제외한 휴일·토요일 행 | 508 |

평일 후보의 차단 사유는 다음과 같다.

| 사유 | 행 수 |
|---|---:|
| `TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE` | 2,772 |
| `TIMETABLE_ROUTE_NUMBER_MISSING_FROM_TAGO_CATALOG` | 162 |
| `TIMETABLE_EFFECTIVE_DATE_MISSING` | 149 |
| `TIMETABLE_MAJOR_STOP_TIMES_INSUFFICIENT` | 133 |
| `TIMETABLE_MULTIPLE_CLOCKS_PER_CELL_UNSUPPORTED` | 70 |
| `TIMETABLE_DEMAND_RESPONSIVE_ROW_UNSUPPORTED` | 31 |

## 결론

현재 일반화된 규칙만으로 183개 노선번호의 2,406개 평일 운행행을 추정 없이 결합할 수 있다.
시행일이 `미입력`인 행도 유효기간을 만들지 않고 제외했다. 그러나 동률 route pattern 2,772개
행이 남아 있으므로 이 결과를 곧바로 전역 publication으로
활성화하지 않는다. 다음 단계는 exact 행만 별도 raw manifest로 만드는 경로를 추가하고, 기존
동부 201·211·212 publication과 동일한 bundle 검증을 통과시키는 것이다. 부분 publication은
`JEJU_ALL/ALL future_bus_planning_ready=1`을 주장하지 않으며, coverage와 실제 조회 범위를
분리해 기록한다.
