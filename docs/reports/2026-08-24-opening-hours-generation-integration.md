# 운영시간 반영 Generate→Evaluate 통합 점검 (2026-08-24)

## 결론

상태는 `Code/DB integration Pass, actual TMAP Generate→Evaluate Pass, selected opening-hours
coverage Partial`이다. 출입구·접근성은 계속 제외했다. 동부 PoC의 active 운영시간 7곳을
버스-only 후보 선택과 일정 제약에 연결했고, 실제 TMAP 보행 경로로 정확히 세 추천과
balanced 선택안 Evaluate를 통과했다.

실제 도로망에서 운영시간 우선 primary가 탈락해 최종 선택은 주로 `UNVERIFIED` 운영시간
fallback을 사용했다. 이는 영업 중이라는 주장이 아니며, 실제 운영시간이 확인된 장소를
선택한 전 구간 라이브 인수까지 완료했다는 의미도 아니다.

## First RED와 수정

첫 RED에서 버스-only 생성은 전체 40회 예산이 남아 있어도 `bus_walk=20`의 고정
하위 상한 때문에 21번째 정류장 연결 보행 호출을 거부했다. 버스-only일 때만 사용하지
않는 직접도보·택시 몫을 버스 연결 보행에 재배정했다. 전체 외부 호출 40회, 150초
timeout, 동시성 4개는 바꾸지 않았다.

두 번째 RED는 후보 SQL이 당일 적용 가능한 active 운영시간을 우선하지 않고 공식
휴무 예외도 후보 단계에서 제외하지 않는 문제를 고정했다. 후보는 이제 다음 순서를
사용한다.

- 요청 필수 장소
- 당일 적용 가능하고 8일 신선도 안에 있는 verified 운영시간 보유 장소
- 버스-only의 숙소 거리와 기존 결정적 tie-break

당일 `CLOSED` 예외는 후보에서 제외하고, 일정 뼈대와 세 전략 조합에서도 운영시간이
확인된 순서를 우선한다. 운영시간이 없는 장소를 가능하다고 추정하지 않으며, 검증 가능한
대안이 없을 때만 기존 `UNVERIFIED` 주의 후보로 남긴다.

## DB와 대체 transport 통합 결과

대표 요청은 동부 scope accommodation인 플레이스 캠프 제주, 2026-08-24
07:00~19:00, 버스-only, 구간당 최대 환승 1회다. 사용자 원문과 GPS는 사용하지 않았다.

| 항목 | 결과 |
|---|---:|
| 후보 장소 | 관광 8 / 식사 5 / 휴식 3 |
| 후보 중 verified 운영시간 | 7곳 |
| 당일 버스 후보 leg | 36,803개 |
| 전략별 primary/fallback | 각 2개 |
| Generate | `success`, 정확히 3개 |
| 전략 | `balanced`, `relaxed`, `experience_max` |
| 전략별 transfer | 6 / 5 / 6 |
| 대체 TMAP 호출 | 31/40 |
| 선택안 운영시간 | 4/4 `VERIFIED` |
| 응답 opening fact | 7개 |
| 선택안 Evaluate | `feasible_with_caution` |
| Evaluate timing/evidence | `at_risk` / `verified` |
| 버스 segment | 5개 |
| 운영시간 판정 | `VERIFIED_OPEN` 4 / `CONFLICT` 0 |

대체 transport는 TMAP adapter의 호출·정규화·메모리 cache 경계를 그대로 통과하되
고정된 보행 요약만 반환했다. 실제 거리·시간의 라이브 정확성을 증명하는 자료로는
사용하지 않는다.

## 실제 TMAP 후속 인수

ignore된 외부 환경파일의 `JEJU_TMAP_API_KEY`와 runtime DSN을 프로세스 메모리에만
주입했다. 키 값은 출력하거나 새 파일에 복사하지 않았다. `tmap.pedestrian`과
`tmap.driving`의 승인·secret·메모리 전용 preflight를 통과했고, 대표 권역 자동차
3구간과 숙소→최근접 confirmed 정류장 보행 1구간 smoke도 통과했다.

첫 실제 요청인 플레이스 캠프 제주에서는 하차 정류장→장소 보행 18개 중 15개가
20분 상한을 넘었다. TMAP 15/15 응답은 정상이었지만 완주 구간이 부족해
`ROUTE_EVIDENCE_MISSING`으로 닫혔다. 안전 상한은 늘리지 않았다.

제주알(R)호텔 요청에서는 운영시간 후보가 기존 근거리 완주 후보를 밀어내 세 전략
뼈대를 만들지 못했다. 다음 세 RED를 순서대로 고정했다.

1. 운영시간 랭킹과 숙소 근거리 랭킹의 bounded union을 만들고 관광 16·식사 10·휴식 6
   이하로 다시 제한한다.
2. primary가 실제 도로망에서 실패할 때의 fallback은 예상 숙소 복귀가 빠른 안을 우선한다.
3. 자동 식사 fallback은 복귀시각보다 식사 장소의 앞선 순서를 우선해 점심 시간창 여유를
   확보한다.

최종 대표 요청은 제주알(R)호텔, 2026-08-24 07:00~19:00, 버스-only, 구간당 최대 환승
1회다. 사용자 원문과 GPS는 사용하지 않았다.

| 항목 | 실제 결과 |
|---|---:|
| TMAP 보행 호출 | 27/40, `PASS` 27 / `FAIL` 0 |
| Generate | `success`, 정확히 3개 |
| 전략 | `relaxed`, `experience_max`, `balanced` |
| 전략별 버스 transfer | 4 / 6 / 6 |
| 택시 혼입 | 0 / 0 / 0 |
| 숙소 복귀 | 18:36 / 18:23 / 17:47 |
| 선택 장소 운영시간 verified | 1/3 / 0/5 / 0/5 |
| balanced Evaluate | `feasible_with_caution` |
| Evaluate timing/evidence | `at_risk` / `partial` |
| Evaluate 버스 segment | 6 |
| 운영시간 충돌 | 0 |
| Evaluate 추가 TMAP 호출 | 0, 프로세스 메모리 cache 재사용 |

TMAP 안전 로그의 latency는 최소 58ms, 중앙값 71ms, 최대 753ms였다. fingerprint·시각·
latency·상태 외에 원문 응답·geometry·좌표·키를 기록하지 않았다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 이중 후보·fallback 집중 회귀: `Pass`
- 전체 offline pytest: `407 passed, 9 skipped`
- v0.6 checksum manifest: `Pass`

## 보호 조건과 잔여 위험

- 승인된 active DB와 `config/data_sources.toml`의 TMAP 계약 외 데이터 소스를 추가하지 않았다.
- TMAP 원문·geometry, 사용자 원문·GPS를 저장하거나 로그로 남기지 않았다.
- 출입구 publication과 접근성 조건은 변경하지 않았다.
- 세 추천을 만들지 못하면 부분 결과 없이 실패하는 공개 계약을 유지했다.
- 실제 TMAP Generate→Evaluate는 완료했지만 선택 일정 13개 장소 중 verified 운영시간은
  1곳뿐이다. 운영시간 7곳의 지리적 coverage와 실제 버스 접근성을 함께 넓히기 전에는
  전역 운영시간 기반 추천 완료로 승격하지 않는다.
