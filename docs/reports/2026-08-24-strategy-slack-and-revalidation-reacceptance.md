# 전략별 이동 여유·재판정 재인수 (2026-08-24)

## 결론

제주알(R)호텔의 2026-08-24 07:00~19:00 버스 전용 일정에 전략별 실행 여유를
정책값으로 반영했다. 최종 라이브 Generate는 서로 다른 추천을 정확히 세 개 반환했고,
선택 장소의 운영시간과 버스 환승 연결은 모두 검증됐다.

| 전략 | 이동 후 여유 | Generate 위험도 | Evaluate | 숙소 복귀 |
|---|---:|---|---|---:|
| `relaxed` | 구간당 20분 | `low` | `feasible`, `on_schedule`, `low` | 17:03 |
| `balanced` | 구간당 10분 | `medium` | `feasible_with_caution`, `at_risk`, `medium` | 17:02 |
| `experience_max` | 0분 | `high` | `feasible_with_caution`, `at_risk`, `high` | 18:23 |

`relaxed`와 `balanced`의 여유는 임의 상수가 아니라
`planning_policy_v1.toml`의 `risk_slack_minutes` 임계값을 사용한다. 여유를 원하지 않는
`experience_max`는 0분을 유지한다.

## 구현과 첫 RED

- 이동 도착 직후 `PLANNED_SAFETY_BUFFER`를 별도 timeline 이벤트로 넣었다.
- 개장·식사 시간창 전 대기는 `OPERATING_OR_MEAL_WINDOW_WAIT`로 분리했다.
- 버스 후보 탐색도 같은 전략 여유를 활동 시작 전에 반영해 후보와 최종 생성의 시간 의미를
  맞췄다.
- Generate와 Evaluate는 같은 정책 임계값으로 segment slack과 전체 위험도를 계산한다.
- 현재 위치가 남은 첫 활동 장소와 같으면 외부 경로를 호출하지 않고
  `ALREADY_AT_DESTINATION` 0분 위치 연속성으로 처리한다. 버스 전용 요청에서도 이를 금지된
  도보로 오판하지 않는다.

첫 라이브 실행은 TMAP 보행 23회가 모두 성공했지만 전체 추천은 안전하게
`insufficient_feasible_routes`로 닫혔다. DB 후보 근사치보다 실제 문-to-문 이동이 길어져
primary가 식사 종료창을 놓쳤고, fallback 일부는 첫 관광지 이후 버스 연결이 성립하지 않았다.
운영시간이나 이동 한도를 완화하지 않고, 후보 선택을 다음 순서로 고쳤다.

1. 운영시간 검증 후보 수
2. 가장 늦은 식사 순서와 전체 식사 순서
3. 세 전략의 공유 route 수
4. 복귀시각과 결정론적 tie-break

이후 최종 라이브 생성은 TMAP 보행 21회 모두 `PASS`로 성공했다. 세 전략 Evaluate는
동일 메모리 cache를 사용해 추가 TMAP 호출 0회였고, 운영시간 issue와 환승 issue도 각각
0건이었다.

## 진행 상태·지연 재판정

최종 `relaxed` 추천의 뒤쪽 활동 두 개를 남긴 상태에서, 실제 DB와 TMAP으로 생성한 일정에
합성 진행 지연을 적용했다. GPS와 사용자 원문은 사용하지 않았고 현재 event·place만으로
위치를 확정했다. 이 단계는 현재 정류장 대기가 아니므로 TAGO snapshot을 호출하지 않았다.

| 활동 시작 지연 | 재판정 | 남은 일정 | 검증된 회복안 |
|---:|---|---|---|
| 10분 | `disrupted` | `infeasible`, `critical` | 없음 |
| 25분 | `disrupted` | `infeasible`, `critical` | `SHORTEN_STAY` 적용 시 `on_schedule` |

지연량과 결과가 단순 비례하지 않는 이유는 재판정 시각에 따라 선택 가능한 다음 공식
버스편이 달라지기 때문이다. 10분 사례는 검증 가능한 수선안이 없어 빈 배열을 유지했고,
25분 사례만 체류 단축 후 전체 남은 일정을 다시 평가해 정상 결과가 확인됐다. 재판정
실행에서는 TMAP 보행 22회가 모두 `PASS`였고, 두 시나리오의 동일 장소 첫 구간은 외부 호출
없이 처리됐다.

## 보호 조건

- 승인된 `config/data_sources.toml`의 active DB와 TMAP만 사용했다.
- TMAP 원문·상세 geometry·좌표·키와 사용자 원문·GPS 이력을 저장하거나 출력하지 않았다.
- 시간·거리·비용은 LLM이 만들지 않았고 정책 fact와 공식 이동 evidence만 사용했다.
- 성공 추천은 정확히 세 개이며, 만들 수 없던 중간 실행은 부분 성공 없이 전체 실패했다.
- 실제 선택 버스의 TAGO same-day 결합은 기존
  [1차 재인수](./2026-08-24-phase1-reacceptance.md) 결과를 유지한다. 이번 단계는 그 계약을
  바꾸지 않고 전략 여유와 활동 지연 회복을 추가 검증했다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 전체 offline pytest: `419 passed, 9 skipped`
- v0.6 checksum manifest: `Pass`
