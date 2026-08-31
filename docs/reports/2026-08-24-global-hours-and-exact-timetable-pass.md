# 전역 운영시간·exact 버스 시간표 순차 작업 결과 (2026-08-24)

> 이 문서는 2026-08-24 당시의 중간 상태를 보존한다. 1,019건 수집 완료와 주간 반복 휴무 모델링을
> 반영한 최신 상태는 [2026-08-25 전역 운영시간 완료 스냅샷](./2026-08-25-global-hours-complete-snapshot.md)을
> 따른다.

## 결론

상태는 `Partial / fail-closed`다. 전역 운영시간과 전역 exact 버스 시간표 모두 완성 조건인
coverage `1.0`에 도달하지 않아 활성화하지 않았다. 기존 권역별 운영시간과 request-scoped exact
버스 계획은 유지되며, 전역 준비도만 계속 닫혀 있다.

| 축 | 현재 측정 | 전역 활성화 |
|---|---:|---:|
| 운영시간 | 26/959 (`0.027112`) | 차단 |
| exact 버스 | 369/974 (`0.378850`) | 차단 |

## 전역 운영시간 기준과 수집

`JEJU_ALL/ALL opening_hours_ready`의 분모를 active TourAPI 장소 중 숙박 유형 `32`를 제외한
959곳으로 고정했다. 특정 단일 여행일에 유효한 `VERIFIED` 기본 규칙이나 공식 `CLOSED`·
`SPECIAL_HOURS` 예외가 있어야 충족으로 센다. 신규 후보 publication과 서로 다른 source의 active
보완 publication은 합집합으로 측정하되, 활성화 때 교체되어 사라질 같은 source의 이전 publication은
분자에서 제외한다.

active 장소 1,019개와 정확히 일치하는 query CSV를 owner-only 경로에 mode `0600`으로 만들었다.
승인된 `tourapi.place-intro` 계약, API host/path, raw private 저장, 27행에서 전역으로 넓히는 검토된
scope baseline을 모두 통과한 뒤 실제 수집을 실행했다.

첫 수집은 836/1,019 query에서 중단됐다. 836개 완료 query 중 실제 원본 행은 772건이고, 64개는
공식 API가 정상적인 빈 결과를 반환했다. 남은 183개 query는 호출하지 않고 즉시 `INCOMPLETE`로
닫았다. 같은 신선한 raw를 지정한 재개도 동일 지점에서 바로 실패했다. 안전한 HTTP 사유 보존을
적용한 뒤 `HTTP_STATUS_429`를 확정했다. v10 정규화 적용 후 한 차례 추가 재개에서도 같은 상태로
836번 다음 query에서 즉시 중단됐으므로, 호출 한도 또는 속도 제한이 회복되기 전에는 재호출하지
않는다.

- 다음 재개 acquisition: `47e5052d-5236-4df0-8311-9ea939e7fde4`
- query 완료율: 82.04%
- raw 행: 772
- 현재 v10 구조화 가능 장소: 531곳
- 생성 가능한 요일별 규칙: 3,635건
- 거부: 241건 (`OPENING_HOURS_UNVERIFIED` 187, 필수 필드 없음 54)
- production activation: `false`

명시적인 복수 주간 휴무 요일 목록을 안전하게 해석해 19곳과 94개 규칙을 추가했다. 남은 검증 불가
187건은 복수 시간대 57, 정확한 시각 없음 45, 계절·가변 27, 월 단위 휴무 13, 공휴일 휴무 14,
요일 근거 없음 12, 비정기 휴무 6, 기타 휴무 9, 복합 주간 휴무 2, 특정 요일 의미 불명확 2건이다.
이들은 날짜별 예외나 추가 공식 의미 근거가 없으므로 자동 해석하지 않는다. 거부 중 숙박 39건은
전역 분모에서 제외된다. 원문 운영시간, 장소 ID, API key는 보고서나 로그에 복사하지 않았다.

## 재개 안전장치

- aggregate 수집은 첫 실패 뒤 남은 query를 계속 호출하지 않는다.
- 동일 source·query 순서·scope baseline의 raw만 재개할 수 있다.
- 성공 완료된 pagination만 재사용하며 누락 query만 새로 호출한다.
- 원본 응답 시도와 manifest를 새 private raw에 먼저 보존한 뒤에만 정규화한다.
- 최초 관측시각을 유지하고 source 계약의 8일 신선도를 넘긴 raw 재개를 거부한다.
- CLI는 완료 query 수와 acquisition UUID를 출력한다.
- HTTP 상태 오류는 응답 본문 없이 안전한 reason code만 보존한다.
- `HTTP_STATUS_429`는 내부 3회 재시도 대상에서 제외하고 첫 응답에서 중단한다.

부분 raw 분석에는 장소별 원문 대신 콘텐츠 유형별 구조화·거부 집계만 추가했다. 전역 1.0이 아니므로
531곳의 규칙도 publication으로 스테이징하거나 active로 올리지 않았다.

## exact 버스 시간표

active 공식 시간표 publication은 `d6985477-936c-4994-ab57-9c81763af636`이며, 2,931 trip과
16,896 stop-time으로 369개 TAGO route pattern을 덮는다. owner-only 공식 workbook 234개와
활성 TAGO 974개 pattern을 다시 대조한 결과 자동으로 더 확정할 수 있는 공식 식별자가 없다.

가장 큰 차단은 동일 노선번호의 여러 TAGO pattern 중 하나를 공식 workbook만으로 유일하게 고를 수
없는 2,427개 평일 후보 행이다. 그 밖에 시행일 누락 149, 주요 정류장 시각 부족 133, 한 셀의 복수
시각 70, TAGO 카탈로그와 불일치 162, 수요응답형 31건이 남는다. 공식 BIS group metadata는
provider route pattern ID를 제공하지 않으므로 schedule ID나 노선번호만으로 운행편을 복제하지
않았다.

따라서 다음 버스 단계는 공식 route-pattern 대응표나 같은 수준의 공식 식별 근거를 확보한 뒤 기존
pinned workbook과 증분 결합하는 것이다. 그 전까지 `JEJU_ALL/ALL
future_bus_planning_ready=1.0`은 발행하지 않는다.

## 보호 조건

- 승인되지 않은 외부 source는 호출하지 않았다.
- TMAP 원문·상세 geometry·사용자 원문은 수집하거나 저장하지 않았다.
- 시간·거리·비용을 새로 만들지 않았고 공식 evidence가 없는 버스 pattern을 연결하지 않았다.
- coverage 1.0 미달값의 발행·활성화를 시도하지 않았다.
- 추천 생성 계약과 정확히 세 개가 아니면 `insufficient_feasible_routes`를 반환하는 규칙은 바꾸지
  않았다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 전체 offline pytest: `427 passed, 9 skipped` (436 collected)
