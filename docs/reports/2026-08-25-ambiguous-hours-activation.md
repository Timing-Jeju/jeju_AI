# 모호한 운영시간의 안전한 전역 활성화 (2026-08-25)

## 결론

TourAPI 상세소개 1,019건의 조회 완전성과 날짜별 정확 운영시간 품질을 분리했다. 모든 active
장소에 조회 결과 상태를 하나씩 보존한 v12 publication은
`opening_hours_snapshot_ready=1.0`을 충족해 활성화했다. 정확한 시각을 판별할 수 없는 장소는
시간을 만들지 않고 `UNVERIFIED`와 `OPENING_HOURS_UNKNOWN`으로 추천할 수 있다. 공식 휴무가
확인된 장소는 계속 여행일 후보에서 제외한다.

| 축 | 결과 | 의미 |
|---|---:|---|
| 상세소개 조회 상태 | 1,019/1,019 (`1.000000`) | 전역 활성화 기준 통과 |
| 2026-08-25 정확 운영 판정 | 637/959 (`0.664234`) | 날짜별 품질지표, 미확인 장소는 주의 후보 |
| exact 버스 route pattern | 369/974 (`0.378850`) | 이번 변경 범위 밖, 전역 완성 아님 |

## v12 publication

완료된 v11 raw를 재사용해 외부 TourAPI를 다시 호출하지 않고 v12로 정규화했다. 장소별 관측
fact에는 구조화 상태와 사유 코드만 저장하며 응답 원문은 projection에 복제하지 않는다.

- acquisition: `d5b6695a-f8f5-4956-8421-531ae020edd3`
- publication: `588b9aed-875b-4f10-9d00-8ed209940e58`
- dataset: `2026-08-24-b667a90215ec`
- 정규화 schema: `tour-place-intro-hours-v12`
- raw 장소 row: 953
- 운영 규칙·주간 휴무 fact: 4,552
- 장소별 관측 fact: 1,019
- 전체 accepted fact: 5,571
- 정규화 거부: 321
- 상태: `PUBLISHED`, production active

| 관측 상태 | 장소 수 |
|---|---:|
| `STRUCTURED` | 632 |
| `PARTIAL` | 32 |
| `UNVERIFIABLE` | 289 |
| `NO_DATA` | 66 |
| 합계 | 1,019 |

`NO_DATA`는 완료된 query에 응답 장소 row가 없었다는 뜻이며 미운영을 뜻하지 않는다. 원문
문구가 있어도 정확한 시각이나 날짜 의미를 안전하게 구조화하지 못한 경우는 `UNVERIFIABLE`,
일부 공식 사실만 구조화된 경우는 `PARTIAL`이다.

## 활성화와 날짜별 품질

활성화는 정확한 운영시간 1.0을 요구하지 않는다. 대신 active TourAPI 장소와 관측 fact가
일대일로 대응하는 전역 snapshot 1.0을 요구한다. 활성화 후 다음 날짜별 정확 운영 판정률을
`COVERAGE_INCOMPLETE` 품질지표로 추가했다.

| 여행일 | 정확 판정/전체 | coverage |
|---|---:|---:|
| 2026-08-25 | 637/959 | `0.664234` |
| 2026-08-26 | 640/959 | `0.667362` |
| 2026-08-27 | 639/959 | `0.666319` |
| 2026-08-28 | 631/959 | `0.657977` |
| 2026-08-29 | 632/959 | `0.659020` |
| 2026-08-30 | 641/959 | `0.668405` |
| 2026-08-31 | 633/959 | `0.660063` |

이 품질지표는 관측이 누락됐다는 뜻이 아니라 정확한 개장·폐장 또는 공식 휴무를 날짜별로
판정할 수 있는 비율이다. 정확한 시각이 없는 나머지는 `null`을 유지한다.

## 런타임 판정

active DB smoke에서 운영시간을 구조화하지 못한 실제 장소는 다음처럼 처리됐다.

- 장소 후보 조회 성공
- `operating_hours_status=UNVERIFIED`
- 원문 없이 `opening_hours_observation` evidence fact 연결
- 정확한 `opens_at`·`closes_at`을 생성하지 않음
- 공식 주간 휴무가 확인된 비교 장소는 후보 조회에서 제외

결정론적 Generate→Evaluate 회귀에서는 서로 다른 추천 세 개 계약을 유지하면서 각 추천의
상태가 `feasible_with_caution`, 운영시간이 `UNVERIFIED`, 전역 경고가
`OPENING_HOURS_UNKNOWN`인지 검증했다. 세 유효 추천을 만들지 못하면 기존과 동일하게 부분
성공 없이 `insufficient_feasible_routes`를 반환한다.

## 남은 범위

TMAP과 TAGO 키가 현재 실행 환경에 없어 새 v12 active DB를 사용한 외부 API 라이브
Generate→Evaluate는 실행하지 못했다. DB runtime smoke와 외부 호출 없는 결정론적 회귀는
통과했다. 키가 준비되면 같은 날 실제 선택 경로로 TMAP/TAGO 종단 검증을 다시 수행해야 한다.
exact 버스의 전역 다의 노선 605개도 공식 route-pattern 대응 근거가 생기기 전에는 자동 연결하지
않는다.

## 보호 조건

- 승인된 source contract 밖의 외부 호출 없음
- TMAP 원문·상세 geometry·사용자 원문 저장 및 로그 없음
- 확인되지 않은 시간·거리·비용 생성 없음
- Pydantic 공개 JSON 계약 변경 없음
- 추천 성공은 정확히 세 개, 미달 시 `insufficient_feasible_routes`
- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 전체 offline pytest: `436 passed, 9 skipped`
