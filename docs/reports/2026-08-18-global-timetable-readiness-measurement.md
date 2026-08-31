# 제주 전역 미래 버스 준비도 측정 결과 (2026-08-18)

## 판정

`Blocked by incomplete coverage`다. 요청일에 정확한 service calendar·trip·stop-time을 가진
route pattern은 활성 공식 TAGO 노선 974개 중 313개이며 전역 coverage는
`0.321355`다. 따라서 `JEJU_ALL/ALL future_bus_planning_ready`는 활성화하지 않았다.

## 측정 변경

이전 측정은 시간표 publication에 이미 들어온 trip만 분모로 사용했다. 부분 발행의 모든 trip이
내부적으로 완전하면 전역 coverage도 1.0으로 보일 수 있었다. 새 측정은 다음을 적용한다.

1. `JEJU_ALL/ALL` 분모는 active `tago.bus-route`의 공식 노선 974개다.
2. 분자는 요청일 service day와 calendar 유효기간을 만족하는 exact trip이 하나 이상 있는
   route fact다.
3. trip은 stop-time이 두 개 이상이고 모든 stop-time 정류장이 같은 timetable publication의
   route-stop에 존재해야 한다.
4. timetable 시행일 범위를 벗어나거나 요청일 `REMOVED` service exception이 있으면 제외한다.
5. 공휴일은 active 공식 holiday fact로 판정하고 그 외에는 요청일 요일을 사용한다.
6. 권역 측정도 동일하게 exact service day·유효기간·제외일을 적용한다.

## 라이브 측정

| scope | numerator | denominator | ratio | activation |
|---|---:|---:|---:|---|
| `JEJU_ALL/ALL` | 313 routes | 974 routes | 0.321355 | 차단 |
| `JEJU_EAST/POC_V1` | 2,586 trips | 2,586 trips | 1.0 | 기존 활성 유지 |

- timetable publication: `ada744d5-2a8a-4e51-8fc4-f75d065ef7b9`
- dataset: `2026-08-18-abb96f90f687`
- service date: `2026-08-18`
- 전역 미달값을 `--execute`로 추가하려는 시도는
  `COVERAGE_NOT_READY_FOR_ACTIVATION`으로 거부됐고 active coverage는 변경되지 않았다.
- 표준 버스-only 요청의 유일한 missing capability는 계속
  `future_bus_planning_ready`다.

## 해석

workbook exact mapping 감사의 42.0409%는 workbook 후보 행 기준이고, 이번 32.1355%는 active
공식 route fact 기준이라 분모가 다르다. 어느 값도 제주 전역 준비도 1.0을 뜻하지 않는다.

정류장 identity 4,273건과 버스 요금은 전역 준비가 끝났지만, 현재 시간표로는 세 전략의 모든
연속 구간과 숙소 복귀를 보장할 수 없다. 시간을 보간하거나 노선 패턴만으로 운행편을 만들지 않고
버스-only 공개 요청을 fail-closed로 유지한다.

## 다음 데이터 작업

1. exact mapping이 빠진 661개 active route pattern의 workbook sheet·시행일·checkpoint를
   공식 근거로 추가 확인한다.
2. 새 timetable publication마다 이 전역 분모로 coverage를 다시 측정한다.
3. coverage 1.0 이후에도 실제 숙소 왕복·식사·휴식·대기 한도로 서로 다른 세 일정이 만들어지는지
   별도 E2E를 통과해야 capability 인수 완료로 판정한다.
