# v0.6 버스 데이터 발행·라이브 인수 결과 (2026-08-18)

## 판정

`Partial Pass`다. 제주 전역 공식 시간표 중 workbook·노선·정류장 순서를 유일하게 확인한
2,406개 운행편과 기존 동부 201·211·212번 180개 운행편을 한 active publication으로
발행했다. 일반 Generate는 서로 다른 세 일정을 반환한다. 버스-only Generate는 택시나 부분
추천을 섞지 않고 `insufficient_feasible_routes / ROUTE_EVIDENCE_MISSING`으로 닫힌다.

## 활성 데이터

| source | publication | dataset | active rows |
|---|---|---|---:|
| `tago.bus-route` | `577530d6-6498-434c-977f-3b6fd68adf99` | `2026-08-17-7e2649c7980d` | 974 |
| `tago.bus-route-stops` | `ae2cffbf-25bc-47f3-b6ae-c44fd7d0fa60` | `2026-08-17-310af4446400` | 51,038 |
| `jeju.bus-timetable` | `ada744d5-2a8a-4e51-8fc4-f75d065ef7b9` | `2026-08-18-abb96f90f687` | trip 2,586 / stop-time 14,967 |
| `transport.stop-identity-map` | `f33e2d0c-9df7-4a57-a4da-2f23bdc9ab8c` | `2026-08-11-dc2e45bb5da7` | confirmed 611 |
| `jeju.bus-fare-policy` | `7b7afe89-026d-4bc9-a4ea-e6d3891dc831` | `2026-08-10-83c780678fd4` | active |

- 새 시간표 publication은 route-stop 16,985건, trip 2,586건, stop-time 14,967건이다.
- 기존 동부 active의 trip 180건과 stop-time 1,321건은 양방향 `EXCEPT` 비교에서 차이가 0건이다.
- 공식 BIS 일정 페이지의 234개 XLSX, 440개 sheet는 owner-only raw에 보관하고 고정
  checksum manifest로 검증했다. 공개 저장소에는 원본 XLSX를 넣지 않았다.
- workbook weekday 후보 5,723행 중 exact mapping은 2,406행, 183개 노선번호,
  281개 route pattern이다. 유일하지 않은 pattern, 시행일 누락, 복수 시각 cell 등은 발행에서
  제외했다.
- `future_bus_planning_ready`, `confirmed_stop_mapping_ready`, `fare_policy_ready`의
  2026-08-18 범위는 `JEJU_EAST/POC_V1`이다. 부분 exact 시간표를 제주 전역 준비도 1.0으로
  표시하지 않았다.

## 구현 결과

1. 전역 timetable parser는 공식 노선번호, weekday, checkpoint 순서, 유일 route pattern,
   시행일을 모두 만족한 행만 exact trip으로 변환한다. 수요응답·복수 시각·모호한 pattern은
   시간을 만들지 않는다.
2. 전역 bundle 생성 시 기존 동부 exact 운행편을 overlay하고, pinned workbook checksum이
   달라지면 raw bundle 생성을 거부한다.
3. 버스-only 후보는 단순히 양 끝에 정류장이 있는 장소가 아니라 같은 exact trip으로 숙소에서
   출발하고 돌아올 수 있는 confirmed 정류장 주변 장소만 남긴다. 실제 접근·하차 도보 20분과
   대기 30분은 이후 TMAP·시간표 단계에서 다시 검증한다.
4. 정류장 쌍은 시간표상 도착 순서대로 검증하고 첫 후보가 실제 도보 제한을 통과하면 다음 쌍을
   호출하지 않는다. 환승은 직통 문-to-문 도착보다 더 빠를 가능성이 있는 경우만 추가 검증한다.
5. 환승 SQL은 confirmed 정류장 611×611 메모리 공간 조인을 제거하고 active bus-stop GiST
   인덱스를 쓰는 transfer-pair CTE로 바꿨다. 동일 실데이터 조회가 6.84초에서 0.58초로
   약 11.8배 단축됐다.
6. append-only stop-time에 endpoint-first 조회 인덱스 migration 0015를 추가했다. 기존
   trip-first 인덱스와 함께 직통·환승의 양방향 조회를 지원한다.
7. 외부 경로 한도는 전체 40회와 직접보행/택시/버스도보 10/10/20회를 유지한다. 생성 timeout도
   현재 150초를 유지했다.

## 라이브 검증

- TMAP: raw body·geometry를 저장하거나 출력하지 않고 endpoint 도보·차량 요약만 메모리
  cache에서 사용했다.
- Generate 다중 수단: 플레이스 캠프 제주 숙소, 2026-08-18 09:00~19:00 요청은
  129.210초에 서로 다른 추천 세 개를 반환했다. 선택 이동은 모두 택시였고 숙소 복귀는
  17:34·18:33·18:36으로 모두 19:00 이전이다. 버스를 억지로 선택하지 않았다.
- Generate 버스-only: 같은 조건에서 23.229초에
  `insufficient_feasible_routes / ROUTE_EVIDENCE_MISSING`을 반환했다. 추천 수와 택시 이벤트는
  모두 0이다.
- 버스-only 사전 시뮬레이션: 후보 endpoint 49개와 exact 직통 운행 옵션 25,843개를 대상으로
  식사·휴식, 대기 30분, 19시 복귀를 적용했을 때 관광지 2·3·4개 전략의 완주 조합은 모두
  0개였다. 따라서 실패를 성공으로 바꾸기 위해 시간을 생성하지 않았다.
- TAGO: 2026-08-18 09:40 KST에 confirmed 정류장 5곳을 조회했다. 천수동 810-1,
  세화환승정류장 201, 대천환승정류장 212에서 현재 계획 노선과 일치하는 도착정보가 확인됐다.
  snapshot TTL은 60초이며 다른 미래 버스 구간에 재사용하지 않았다.
- 성공한 Generate 일정에는 버스 구간이 없으므로 임의의 버스 타임라인을 만들어
  Revalidate하지 않았다. 실제 선택 버스 일정이 생길 때 동일 정류장·노선·현재 탑승 의사결정에만
  TAGO snapshot을 적용해야 한다.

## 품질 게이트

- 한글 테스트 설명 검사: Pass
- Ruff: Pass
- Pyright: 0 errors
- offline pytest: 366 passed, 9 skipped
- Pydantic schema drift·synthetic example drift·checksum manifest: Pass

## 남은 제한

- 공식 workbook 후보 중 exact mapping이 유일하지 않은 행과 시행일이 없는 행은 계속 제외된다.
- confirmed stop identity는 동부 POC 범위이므로 제주 전역 bus-only readiness는 fail-closed다.
- 현재 라이브 bus-only 요청은 세 개의 실제 완주 경로를 만들지 못한다. exact checkpoint와
  confirmed identity가 더 촘촘해진 뒤 재검증해야 한다.
- TAGO same-day Revalidate 성공은 Generate가 선택한 실제 버스 일정과 현재 정류장·노선이 모두
  일치할 때만 수행한다.
