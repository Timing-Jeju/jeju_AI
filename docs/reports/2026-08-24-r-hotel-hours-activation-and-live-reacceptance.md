# 제주알호텔 운영시간 활성화·라이브 재인수 (2026-08-24)

## 결론

제주알(R)호텔, 2026-08-24 07:00~19:00, 버스 전용·최대 1회 환승 조건에서
서로 다른 `balanced`·`relaxed`·`experience_max` 추천을 정확히 세 개 생성했다.
선택된 활동의 운영시간은 세 전략 모두 100% 검증됐고, 모든 일정이 19시 전에 숙소로
복귀했다. `balanced` 전체 타임라인의 Evaluate도 운영시간 충돌과 환승 연결 문제 없이
완료됐다.

이 보고서는
[`2026-08-24-r-hotel-opening-hours-expansion.md`](./2026-08-24-r-hotel-opening-hours-expansion.md)의
4/5 staged 상태 이후 작업을 이어받은 최종 활성화·재인수 기록이다.

## 안전 규칙 보강

- `contentTypeId=15` 행사 운영시간은 `eventstartdate`와 `eventenddate`가 모두 유효해야
  정규화하며, 두 날짜를 `valid_from`·`valid_to`로 보존한다.
- 적용 가능한 검증 운영시간이 없는 행사는 자동 발견 후보에서 제외한다. 사용자가 명시한
  장소 ID는 이 자동 필터로 숨기지 않는다.
- `매주 X요일` 휴무는 해당 요일을 제외한 service day로 정규화한다. SNS 공지, 점포별
  상이함, 월별 가변 휴무는 계속 거부한다.
- 완료된 식사·휴식은 다음 연속 활동 시간에 다시 더하지 않도록 후보 탐색과 실제 일정
  생성 계산을 판정 엔진의 의미와 맞췄다.
- 버스 fallback은 검증 운영시간을 우선하고, 같은 조건에서는 primary와 공유하는 구간이
  적은 경로를 먼저 선택한다.

정규화 의미 변경은 승인 계약의 `tour-place-intro-hours-v9`로 구분했다. Pydantic 공개
JSON 계약과 정확히 세 추천 또는 `insufficient_feasible_routes` 불변조건은 바꾸지 않았다.

## publication과 coverage

복수 서비스 범위를 한 manifest에 안전하게 보존하도록 scope bundle 검증 키를
`(region_code, grid_id, ...)`로 확장하고 `service-scope-v2`로 올렸다. 기존
`JEJU_EAST/POC_V1`을 그대로 유지하면서 `JEJU_R_HOTEL/BUS_CANDIDATE_V1`을 추가했다.

| 항목 | 결과 |
|---|---|
| active scope publication | `8f65bcc8-ac80-45f7-812f-a926051bd327` |
| active scope dataset | `2026-08-24-8023e04e4e15` |
| R호텔 scope | 숙소 1곳 + 필수 운영시간 장소 4곳 |
| 최종 TourAPI query | 27개 bounded 상세 ID |
| 공식 상세 raw 행 | 27 |
| 정규화 규칙 | 141 |
| 거부 | 6 |
| active hours publication | `d51f32e2-20d9-4437-996b-a0bdbd92729d` |
| active hours dataset | `2026-08-24-133d3d2c41e1` |
| coverage | `JEJU_R_HOTEL/BUS_CANDIDATE_V1`, 2026-08-24, `1.0` |

27행으로 확대한 첫 시도는 기존 13행 대비 범위 확대를 명시하지 않아
`SOURCE_ROW_CHANGE_RATIO_EXCEEDED`로 차단됐다. 이전 active 원본 행 수 13을 검토된
baseline으로 명시한 새 acquisition만 staged·coverage 측정·활성화했다.

신규로 구조화된 시내 후보에는 동문재래시장, 신산공원, 용연구름다리, 용두암, 용연,
용담해안도로, 사라봉공원, 어영공원, 제주웰컴센터 등이 포함된다. 서문공설시장은 공식
운영·휴무 필드에 모두 `점포별 상이함`이 있어 승격하지 않았고, 제주통일관은 운영시간
값이 없어 계속 미확인으로 유지했다.

## 후보와 실제 라이브 결과

활성화 직후 전략별 primary·fallback 여섯 개를 공식 시간표로 다시 계산했으며, 여섯 개
모두 운영시간 미확인 장소가 0곳이었다. 최종 라이브 호출 전 `tmap.pedestrian`과
`tmap.driving` source preflight가 모두 `PASS`했다.

| 전략 | 장소 수 | 운영시간 검증 | 숙소 복귀 | 버스 transfer | bus ride |
|---|---:|---:|---:|---:|---:|
| `relaxed` | 4 | 4/4 | 15:03 | 5 | 6 |
| `balanced` | 5 | 5/5 | 17:59 | 6 | 6 |
| `experience_max` | 5 | 5/5 | 18:35 | 6 | 6 |

세 일정 모두 택시·직접 도보 transfer 없이 버스 전용으로 생성됐다. TMAP은 보행 접근
검증 27회만 호출됐고 모두 `PASS`했다. 지연시간 집계는 최소 55ms, 중앙값 74ms,
최대 186ms였다.

`balanced` 전체 타임라인 Evaluate 결과는 다음과 같다.

- `status=feasible_with_caution`
- `timing_status=at_risk`
- `evidence_status=verified`
- `overall_risk=high`
- 버스 segment 6개
- 운영시간 issue 0개
- 환승 연결 issue 0개
- Generate 메모리 cache 재사용으로 Evaluate 추가 TMAP 호출 0회

운영시간과 환승 근거는 검증됐지만 시간 여유 위험까지 제거된 것은 아니므로
`feasible_with_caution`과 `at_risk`를 성공으로 과장하지 않는다.

## 중간 안전 거부

실제 경로 검증 과정에서 `REST_VENUE_UNAVAILABLE`과 `ROUTE_EVIDENCE_MISSING`이 각각
재현됐다. 전자는 휴식 완료 시간을 새 연속 활동 구간에 잘못 더하던 계산을 RED 테스트로
수정했다. 후자는 운영시간을 미확인 상태로 낮추거나 버스 접근 기준을 완화하지 않고,
승인된 TourAPI 상세 범위를 시내 후보로 확장해 해결했다. 두 경우 모두 부분 추천 없이
`insufficient_feasible_routes`가 반환됐다.

## 보호 조건

- `config/data_sources.toml`에 승인된 TourAPI와 TMAP만 호출했다.
- TourAPI 원문은 private versioned object storage에 먼저 저장하고 검증한 acquisition에서만
  정규화했다.
- TMAP 원문·상세 geometry·좌표·키는 저장하거나 출력하지 않았다.
- 사용자 원문은 사용하거나 저장하지 않았다.
- 시간·거리·비용을 LLM이 만들지 않았고 생성·판정은 기존 evidence fact만 사용했다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 전체 offline pytest: `417 passed, 9 skipped`
- v0.6 checksum manifest: `Pass`
