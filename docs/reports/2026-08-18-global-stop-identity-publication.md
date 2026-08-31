# 제주 전역 정류장 identity 발행 결과 (2026-08-18)

## 판정

`Pass`다. 활성 공식 TAGO 정류장 4,273건을 공식 provider stop ID 기준으로 모두
`CONFIRMED` identity로 발행했다. 기존 동부 611건은 canonical ID와 source fact ID를 그대로
보존했고, 현재 정확 시간표가 참조하는 정류장 687곳의 identity 누락은 0건이다.

이 발행은 정류장 식별 준비도만 확장한다. 부분 시간표와 요금 coverage를 제주 전역 버스 계획
준비도로 과장하지 않으며, 표준 버스-only 요청은 남은 capability가 준비될 때까지 계속
fail-closed한다.

## 활성 publication

| source | publication | dataset | active rows |
|---|---|---|---:|
| `transport.stop-identity-map` | `17bf7caf-c9e1-4739-8eb2-111987eb427e` | `2026-08-18-65908119654b` | 4,273 |

- 4,273건 모두 `mapping_status=CONFIRMED`이며 `source_fact_id`도 4,273개로 유일하다.
- canonical ID는 `jeju.stop:tago:{provider_stop_id}` 형식이다.
- 공식 provider stop ID와 fact ID의 중복은 각각 0건이다.
- route-stop 좌표가 있는 정류장은 active stop 좌표와 교차검증했다.
- route-stop에서 참조하지 않는 active 정류장 8곳도 공식 ID 근거로 포함했다.
- active에서 내려간 정류장 두 곳은 route 240의 route-stop 7행에 남아 있어 identity에서 제외했다.
  - `tago.bus-stop:JEB406002173`: 3행
  - `tago.bus-stop:JEB406002174`: 4행
- 근거 source는 승인된 `tago.bus-stop`과 공공데이터포털 공식 TAGO 정류소정보 API다.

## 원자적 확대 절차

1. 611건에서 4,273건으로 늘어난 최초 수동 import는 기존 행 변화율 gate가
   `SOURCE_ROW_CHANGE_RATIO_EXCEEDED`로 거부했다. 해당 acquisition은 append-only
   `QUALITY_FAILED` 이력으로 보존했다.
2. 운영자가 검토한 직전 active 행 수 `611`을 명시하는 scope 확대 옵션을 추가했다.
3. 실제 active 기준이 명시값과 다르면 `SOURCE_SCOPE_EXPANSION_BASELINE_MISMATCH`로 중단한다.
4. 일치한 새 publication만 staging한 뒤 측정한 coverage 두 건을 함께 발행해 active snapshot을
   원자적으로 교체했다.
5. 교체 후 기존 611 canonical/source pair의 `EXCEPT` 차이는 0건이다.

## coverage와 런타임 확인

| capability | region/grid | measured coverage |
|---|---|---:|
| `confirmed_stop_mapping_ready` | `JEJU_ALL/ALL` | 1.0 |
| `confirmed_stop_mapping_ready` | `JEJU_EAST/POC_V1` | 1.0 |

- 전역 coverage 분모는 scope manifest 일부가 아니라 active 공식 TAGO 정류장 전체다.
- exact stop-time이 참조하는 distinct stop 687곳 중 active identity 누락은 0곳이다.
- 공개 기본 서비스의 동부 숙소 버스-only 요청은 추천 0건과 `DATA_NOT_READY`를 반환했다.
- 현재 남은 global missing capability는 `fare_policy_ready`와
  `future_bus_planning_ready`다. 정류장 identity는 더 이상 missing 목록에 없다.

## 데이터 보호

- 생성 CSV와 coverage CSV는 `tmp/private` 아래 owner-only mode `0600`으로 유지했다.
- 해당 파일은 Git ignore 대상이며 공개 저장소나 로그에 원문을 추가하지 않았다.
- TMAP raw response, geometry, 사용자 원문은 이 작업에서 저장하거나 출력하지 않았다.

## 다음 순서

1. 섬 전역에 동일하게 적용되는 공식 버스 요금의 global coverage를 측정·발행한다.
2. `future_bus_planning_ready`는 현재 publication 내부 trip 완전성만 보지 말고 전역 요구 노선·정류장
   범위를 분모로 측정하도록 강화한다.
3. 정확 시간표 coverage와 실제 연속 구간 완주성이 모두 충족된 경우에만 global bus-only를 연다.
