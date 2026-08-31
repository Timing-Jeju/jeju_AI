# 제주 전역 버스 요금 준비도 발행 결과 (2026-08-18)

## 판정

`Pass`다. 활성 공식 버스 요금 publication의 STANDARD·EXPRESS 두 등급이 모두
2026-08-18에 유효함을 projection에서 직접 측정하고 `JEJU_ALL/ALL` coverage 1.0을
append-only로 발행했다.

## 근거

| 항목 | 값 |
|---|---|
| source | `jeju.bus-fare-policy` |
| publication | `7b7afe89-026d-4bc9-a4ea-e6d3891dc831` |
| dataset | `2026-08-10-83c780678fd4` |
| capability | `fare_policy_ready` |
| scope | `JEJU_ALL/ALL` |
| service date | `2026-08-18` |
| measured coverage | `1.0` |

- coverage는 설정 파일의 값을 복사해 입력하지 않고 active projection에서 STANDARD와 EXPRESS
  요금 등급을 직접 세어 2/2일 때만 1.0으로 판정했다.
- 기존 active publication을 수정하지 않고 날짜·scope가 다른 coverage 행을 append했다.
- 승인 source는 `config/data_sources.toml`의 `jeju.bus-fare-policy`다.

## 런타임 확인

동부 숙소의 표준 버스-only 요청을 다시 실행한 결과는 추천 0건,
`insufficient_feasible_routes / DATA_NOT_READY`다. missing capability는 이전의
`fare_policy_ready,future_bus_planning_ready`에서 `future_bus_planning_ready` 하나로 줄었다.

시간표 coverage가 준비되지 않은 상태에서 버스를 선택하거나 택시를 섞지 않았으므로 공개
세 추천 불변조건과 fail-closed 경계를 지켰다.

## 다음 순서

`future_bus_planning_ready` 측정 분모를 publication에 이미 포함된 trip만으로 두지 않고 제주
전역의 요구 노선·정류장·service date로 확장한다. 그 측정이 1.0이고 실제 세 전략의 모든 연속
구간이 완주 가능할 때만 전역 버스 계획 capability를 연다.
