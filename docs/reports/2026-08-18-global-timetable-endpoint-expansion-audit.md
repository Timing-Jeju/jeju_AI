# 제주 전역 시간표 시작·종점 exact 확대 감사 (2026-08-18)

## 판정

`Partial Pass`다. 공식 workbook checkpoint 일치 수가 같은 TAGO route pattern 중, 첫·마지막
시각 checkpoint가 실제 route-stop의 시작·종점을 모두 덮는 후보가 정확히 하나인 경우만 exact
mapping으로 추가했다. 185개 평일 운행행과 48개 route pattern을 추정 없이 더 확정했다.

## 추가 판정 규칙

기존 checkpoint 이름·순서·시각·시행일 검증을 모두 통과한 후보에만 다음 tie-break를 적용한다.

1. 가장 많은 checkpoint가 일치한 동률 pattern만 대상으로 한다.
2. 첫 번째로 결합된 checkpoint의 route sequence가 그 pattern의 첫 sequence여야 한다.
3. 마지막으로 결합된 checkpoint의 route sequence가 그 pattern의 마지막 sequence여야 한다.
4. 위 두 조건을 모두 만족하는 pattern이 정확히 하나일 때만 선택한다.
5. 만족 후보가 없거나 둘 이상이면 `TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE`로 계속 제외한다.

workbook 파일 번호나 route number만으로 provider route pattern을 선택하지 않았다. 공식
route-stop 시작·종점과 workbook의 검증된 시각 checkpoint 관계만 사용했다.

## 감사 결과

| 항목 | 기존 | 확대 후 | 변화 |
|---|---:|---:|---:|
| exact trip rows | 2,406 | 2,591 | +185 |
| exact route numbers | 183 | 189 | +6 |
| exact route patterns | 281 | 329 | +48 |
| exact trip row ratio | 42.0409% | 45.2735% | +3.2326%p |
| pattern 동률 제외 행 | 2,772 | 2,587 | -185 |

동부 201·211·212 exact overlay를 포함한 새 owner-only bundle도 typed 검증을 통과했다.

| bundle 항목 | 값 |
|---|---:|
| workbooks | 234 |
| route-stop | 18,438 |
| scheduled trip | 2,771 |
| scheduled stop-time | 15,945 |
| combined route numbers | 192 |
| combined route patterns | 361 |
| ZIP bytes | 2,128,966 |
| ZIP SHA-256 | `178653f3fd588daeb0212456b1943ba0e881b5e090765c9645eb84adcf750f7a` |

## 남은 차단 사유

| 사유 | 행 수 |
|---|---:|
| `TIMETABLE_ROUTE_PATTERN_NOT_UNIQUE` | 2,587 |
| `TIMETABLE_ROUTE_NUMBER_MISSING_FROM_TAGO_CATALOG` | 162 |
| `TIMETABLE_EFFECTIVE_DATE_MISSING` | 149 |
| `TIMETABLE_MAJOR_STOP_TIMES_INSUFFICIENT` | 133 |
| `TIMETABLE_MULTIPLE_CLOCKS_PER_CELL_UNSUPPORTED` | 70 |
| `TIMETABLE_DEMAND_RESPONSIVE_ROW_UNSUPPORTED` | 31 |

따라서 이 확대도 `JEJU_ALL/ALL future_bus_planning_ready=1`을 만들지 않는다. active route
분모 기준 예상 pattern coverage는 361/974이며, 실제 값은 새 publication을 발행한 뒤 다시
측정한다.

## 데이터 보호

- 원본 workbook 234개와 생성 ZIP은 Git ignore된 `tmp/private`에만 두었다.
- 새 ZIP은 mode `0600`이고 workbook 원문·셀 값은 보고서나 로그에 기록하지 않았다.
- 시간은 공식 workbook cell에서 읽은 값만 보존했으며 보간하지 않았다.
