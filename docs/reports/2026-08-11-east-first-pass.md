# 제주 동부 relaxed first-pass

기준일: 2026-08-11  
여행일: 2026-08-14 금요일 09:00~19:00  
상태: `Partial`  
공개 추천 여부: `production_recommendation=false`

## 상태

고정 relaxed 순서에 대해 도보·버스·택시를 같은 출발시각 기준으로 비교하고, 선택 결과를
택시 호출 대기와 주행이 분리된 단일 타임라인으로 조립하는 결정론 경로를 구현했다. 이
first-pass는 정식 세 추천 성공 응답이 아니며 공개 `recommend_jeju_day_trips`의 세 추천 또는
전체 실패 계약을 변경하지 않는다.

고정 7개 이동 구간 모두 201번 직통 route pattern 후보가 있고, 공식 XLSX로 정확한 주요
정류장 시각까지 우선 확정 가능한 후보는 숙소권 고성→성산 1개다. 나머지 후보는 정류장
순서만 확인된 `unverifiable` 대안으로 남긴다. 결정론 synthetic E2E에서 선택된 수단은
도보 3구간, 택시 4구간, 버스 0구간이다. 이는 선택 정책 검증값이지 운영 추천값은 아니다.
검증 입구가 없으므로 대표좌표를 이용한 live preview의 실제 TMAP 시간·거리·요금과 선택
집계는 문서에 승격하지 않는다.

## 검증 근거

| 단계 | 상태 | 확인 내용 |
|---|---|---|
| Generate | 완료 | 15분 도보 우선, 버스·택시 시간/비용 비교, 호출 대기 분리, 대안 보존, 19시 수선 순서 |
| Evaluate | 완료 | 겹침·duration·숙소 왕복·비용/거리 합계·복귀 여유 검사, `schedule_window_fit` 분리 |
| Revalidate | 완료(synthetic) | 정상 `on_schedule`, 20분 지연 `at_risk`, 오조포구 90→60분 `SHORTEN_STAY` 재검증 |

## 2026-08-14 active 근거

| capability | source | publication | 8월 14일 coverage |
|---|---|---|---:|
| service area | `spatial.jeju-boundary` | `23e3c313-7e66-4421-95a7-adbdf1445c78` | 1.0(상시) |
| place search | `tourapi.place` | `e8589da0-2c32-4347-9182-f9f7f15c8fa9` | 1.0(상시) |
| scope manifest | `travel.service-scope-manifest` | `efa51d7d-151e-4e3d-aaaf-7d3003508516` | 1.0 |
| confirmed stop mapping | `transport.stop-identity-map` | `f33e2d0c-9df7-4a57-a4da-2f23bdc9ab8c` | 1.0 |
| weekday timetable | `jeju.bus-timetable` | `2d49ecf2-8fe0-4ac4-a3a6-4ce70e3645b6` | 1.0 |
| bus fare | `jeju.bus-fare-policy` | `7b7afe89-026d-4bc9-a4ea-e6d3891dc831` | 1.0 |
| taxi fare | `jeju.taxi-fare-policy` | `927c03bd-3217-4b44-b380-2135f82b7702` | 1.0 |

평일 시간표 raw ZIP의 SHA-256은
`568d677bd646f28799d345f3587f8d6b8274d6cac01f48e4a9386e0ac6c67b67`이다. 활성 projection은
route-stop 4,239건, 평일 calendar 1건, trip 180건, stop-time 1,321건이다. coverage는 이미
발행된 immutable fact를 8월 14일 범위로 append 측정한 것이며 기존 행을 수정하지 않았다.

공개 `recommend_jeju_day_trips`와 같은 서비스 경로에서 운영시간 미확인을 비차단 주의
상태로 바꿨다. 8월 14일 09:00~19:00 `cost_time_balance` live 실행은 60초·40회 제한 안에서
13.1초에 서로 다른 추천 3개를 생성했다. relaxed 결과의 선택 수단은 택시 4구간·도보
1구간·버스 0구간이며, 선택하지 않은 버스 후보는 비교 대안과 미선택 근거로 보존된다.

동일 relaxed 타임라인의 live Evaluate는 3.8초에 `unverifiable`로 끝났고
`schedule_window_fit=true`를 확인했다. 판정을 막은 것은 시간 산술이 아니라 활성 운영시간
부재에 따른 `OPENING_HOURS_UNKNOWN`이다. live Revalidate는 선택 버스가 없어 TAGO 적용 대상이
아니었으며, 현재 위치 출발·원 숙소 복귀 경계를 보존한 남은 일정을 6.0초에 생성했다.
남은 일정도 운영시간 미확인으로 `unverifiable`이므로 최상위 상태는 구조화된
`data_unavailable`이지만 timeout은 발생하지 않았다.

relaxed의 첫 두 택시 구간에는 exact stop-time을 가진 feasible 버스 대안이 있었지만 각각
`TAXI_BUS_TIME_PENALTY_EXCEEDS_30_MINUTES`로 미선택됐다. 다음 두 택시 구간은 정확한 시간표
버스가 없었고 마지막 구간은 `WALK_WITHIN_15_MINUTES`였다. 공식 버스 시각이 PostgreSQL의
UTC offset으로 노출되던 결함은 `BusRide` 생성 시 `Asia/Seoul`로 정규화하고 Pydantic에서
`+09:00`을 강제하도록 수정했다.

## 검증 공백과 다음 조치

| 문제 | 8월 14일 1차 영향 | 다음 조치 | 완료 조건 |
|---|---|---|---|
| 검증 입구 0건 | 장소 대표좌표 endpoint로 이동하며 승하차 위치 오차 가능 | 가능한 장소부터 입구 수집 | 일반 추천에는 비차단, 이동보조 요청은 verified 입구 필요 |
| 운영시간 0건 active | 생성·안전도 `UNVERIFIED/unknown` 주의 | 날짜별 운영시간 fact 발행 | relaxed 장소 전체 시간 coverage 1.0 |
| 일부 정류장 exact stop-time 없음 | 해당 버스는 대안만 표시 | 공식 checkpoint와 provider ID 추가 대조 | 필요한 승·하차 stop-time exact coverage 1.0 |
| 8월 15일 공휴일 근거 없음 | 8월 14일 결과에는 비차단 | 공식 광복절 운행 근거 확보 | `BUS_SERVICE_DAY_UNVERIFIED` 해소 |
| 선택된 버스 구간 없음 | TAGO를 적용할 구간이 없어 정적 남은 일정만 재계산 | 버스 선택 일정으로 당일 재실행 | 선택 버스 구간에 TAGO 도착정보 적용 |
| 운영시간 미확인 | 실제 재판정은 `data_unavailable` | 날짜별 운영시간 fact 발행 | 남은 일정 `feasible*` 판정 |
| 택시 배차 API 없음 | 10분 계획 버퍼 사용 | 비실시간 표시 유지 | 실제 배차값으로 표현하지 않음 |

## 잔여 위험

- 대표좌표 endpoint 구간은 실제 출입구·승하차 지점과 달라 도보·차량 수치에 오차가 있을 수 있다.
- 시각 없는 버스 route pattern에는 도착·대기시간을 보간하지 않는다.
- 실제 TMAP 수치와 원문·geometry는 프로세스 메모리 밖에 남기지 않는다.
- 승인된 TourAPI place-intro 13개 집계는 수집됐지만 검증 운영시간 정규화가 0건이어서
  `QUALITY_FAILED/NORMALIZED_RECORDS_EMPTY`로 닫고 publication을 만들지 않았다.
- 8월 14일 live 세 추천은 성공했지만 모두 운영시간 미확인 주의 상태다.
- 동일 타임라인 Evaluate와 10초 이내 Revalidate는 연결됐지만 운영시간이 미확인이므로 결과
  상태는 `Partial`이다.
- 함덕·월정리해안도로는 2026-08-14 `east-poc-v2` publication에서
  `JEJU_EAST/POC_V1` 구성원으로 활성화했다. 함덕 필수 공개 생성은 세 추천 모두 19시 전
  복귀했고 한 실행의 balanced에서 버스 1구간이 선택됐다. 후속 재실행은 버스 0구간으로
  수단 선택 경계가 변동했으며, 월정리를 함께 넣은 E2E는 아직 미검증이다.

## 다음 목표

2026-08-14 평일 시간표·route-stop·정류장 identity·버스/택시 요금 publication은 활성화했다.
세 전략 live 생성, 동일 타임라인 Evaluate, 현재 위치 출발·원 숙소 복귀 Revalidate까지
완료했다. 다음 목표는 운영시간 coverage 1.0을 확보하고, 버스가 실제 선택된 일정에서 TAGO
재판정까지 통과하는 것이다. 그 전까지 운영시간은 `UNVERIFIED` 주의로 유지한다.

후속 목표의 구현·차단 상태는
[`2026-08-11-sequential-goals-status.md`](2026-08-11-sequential-goals-status.md)에 기록한다.
