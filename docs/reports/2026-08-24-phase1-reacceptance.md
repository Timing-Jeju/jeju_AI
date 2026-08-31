# v0.6 1차 재인수 결과 (2026-08-24)

> 후속 상태: 이 재인수 시점에 0건이던 운영시간은 같은 날 별도
> raw-first 작업으로 `JEJU_EAST/POC_V1` 7곳, 2026-08-24~30 범위가 활성화됐다.
> 상세한 범위와 제한은 [운영시간 1차 결과](./2026-08-24-opening-hours-first-pass.md)를 따른다.

## 결론

상태: `Phase 1 Pass / selected-bus same-day TAGO verified`

최신 v0.6 코드·공개 계약·격리 저장소 검증에 더해, 오늘자 공식 시간표를 raw-first로
발행하고 실제 Generate가 선택한 버스 일정에 TAGO snapshot을 결합한
Generate→Evaluate→Revalidate 라이브 인수를 완료했다. 성공 추천은 기존 불변조건대로 정확히
세 개이며, 실시간 재판정은 현재 진행 이벤트·지연·선택 노선·confirmed 승차 정류장을 반영해
남은 일정을 다시 계산했다.

## 오늘자 공식 시간표 발행

제주버스정보시스템 공지 화면을 2026-08-24까지 수동 검토했다. 새 공지 중 시가퍼레이드 취소는
2026-08-15만, 방학 맞춤형 버스 운휴는 늦어도 2026-08-11까지만 적용돼 8월 24일 평일 운행을
변경하지 않았다.

owner-only 공식 workbook 234개를 고정 checksum으로 다시 읽어 아래 private raw ZIP을 만들었다.
파일은 Git ignore 경로에 mode `0600`으로 보관한다.

| 항목 | 값 |
|---|---:|
| raw SHA-256 | `e11c14825278a64c6119ce6071b19478e9c907076ca7a6a8454cf2a687b535cc` |
| route-stop | 18,713 |
| scheduled trip | 2,931 |
| scheduled stop-time | 16,896 |
| exact route pattern | 369 |

manual-import dry-run을 통과한 뒤 private MinIO에 원본을 먼저 등록하고 STAGED publication을
만들었다. 전역 측정값은 `369/974 = 0.378850`이므로 전역 coverage 1.0은 발행하지 않았다.
검증된 `JEJU_EAST/POC_V1` 단일 날짜 coverage만 1.0으로 발행해 원자적으로 활성화했다.

| source | active publication | dataset |
|---|---|---|
| `jeju.bus-timetable` | `d6985477-936c-4994-ab57-9c81763af636` | `2026-08-24-753e8465692e` |

오늘 날짜의 버스 요금 정책도 active publication에서 직접 재측정해
`JEJU_ALL/ALL fare_policy_ready=1.0`을 append했다. confirmed 정류장 mapping은 기존
`JEJU_ALL/ALL=1.0`을 유지한다. 2026-08-24는 active 공휴일 fact에 존재하지 않는다.

## First RED와 최소 GREEN

라이브 실행에서 세 결함을 순서대로 재현한 뒤 각각 한글 목적 RED를 먼저 추가했다.

1. `waiting_bus`의 현재 이벤트가 transfer일 때 남은 일정의 첫 버스 수단 제약이 사라졌다.
   현재 transfer를 다음 활동까지의 고정 버스 구간으로 보존했다.
2. 원 일정 평가가 실시간 12회 예산과 요청별 내부 경로 예산을 남은 일정과 공유했다.
   원 일정 평가 예산과 실시간 예산을 분리하고, 남은 일정용 evidence 객체도 새로 만든다.
3. 현재시각 버스 재탐색이 계획시각에 선택된 노선과 다른 최적편을 반환하면 TAGO가 결합되지
   않았다. 공개 계약을 늘리지 않고 full-timeline의 현재 transfer를 찾아 계획시각 공식 경로를
   독립 재조회한다. 노선 ID·노선번호·승하차 정류장·출도착 시각 여섯 claim이 모두 같을 때만
   선택편에 실시간 대기시간을 적용한다.

확인되지 않은 노선·정류장·시각은 계속 기존 정적 경로 또는 구조화된 unavailable 상태로
닫히며, LLM이나 사용자 입력 숫자로 보정하지 않는다.

## 선택 버스 당일 E2E

대표 요청은 `제주알(R)호텔`, 2026-08-24 07:00~19:00, 버스-only, 구간당 최대 환승 1회다.
사용자 원문과 GPS는 입력하거나 저장하지 않았다.

| 단계 | 결과 |
|---|---|
| Generate | `success`, 서로 다른 추천 3개 |
| 전략별 버스 구간 | experience_max 6 / balanced 6 / relaxed 5 |
| 택시 혼입 | 0 / 0 / 0 |
| 숙소 복귀 | 18:11 / 18:11 / 15:33, 모두 19:00 이전 |
| 선택 일정 Evaluate | `feasible_with_caution`, timing=`at_risk`, evidence=`partial`, 버스 segment 6 |
| TAGO 후보 확인 | 선택 정류장 1회 조회에서 계획 노선 일치 |
| 선택 버스 | 415번, 계획 출발 09:18 |
| Revalidate | status/timing=`disrupted`, evidence=`partial` |
| 실시간 결합 | `realtime_bus_arrival` fact 1건, 남은 일정 segment 1건에 포함 |
| 남은 일정 | `infeasible` |
| 회복안 | 검증 가능한 대안 0건이므로 빈 배열 유지 |

재판정 시각이 09:18 계획 출발보다 늦었으므로 `disrupted`는 의도한 안전 판정이다. TAGO 값으로
정적 대기시간을 교체했지만, 19시 복귀까지 검증되는 회복안이 없어 임의 택시·단축 일정을
만들지 않았다. 위치 근거는 GPS가 아니라 현재 event·confirmed stop이므로
`LOCATION_EVENT_BASED`, 적용 범위는 현재 승차 의사결정 하나이므로
`REALTIME_CURRENT_BOARDING_DECISION_ONLY` 경고를 보존했다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- realtime·revalidation·예산 집중 회귀: `25 passed`
- Pydantic Schema drift·v0.6 synthetic example drift: 전체 pytest에 포함
- 운영 DB/MinIO와 분리한 PostGIS·MinIO live 회귀: `9 passed`
- 전체 offline pytest: `398 passed, 9 skipped`

## 보호 조건과 잔여 범위

- `config/data_sources.toml`의 승인 source 외 호출은 추가하지 않았다.
- TMAP raw body·상세 geometry, TAGO raw body, 사용자 원문, GPS 이력을 저장하거나 출력하지
  않았다. 보고서에는 공개 노선번호와 안전한 집계만 남긴다.
- TAGO snapshot은 memory-only이며 TTL 60초와 현재 승차 정류장 1곳 제한을 유지한다.
- 생성 40회·판정 30회·실시간 12회 예산과 동시성 4개를 늘리지 않았다.
- 성공 시 추천 세 개, 아니면 부분 성공 없이 `insufficient_feasible_routes` 규칙을 유지한다.
- 이 재인수 시점의 active opening rule은 0건이었다. 후속 운영시간 발행 전의
  재현 결과로서, 영업시간을 추정하지 않고 `evidence_status=partial`로 표시했다.
- 전역 exact 시간표는 37.8850%이므로 전역 준비도 1.0으로 주장하지 않는다. 요청 주변 exact
  stop-time이 확인된 경우에만 request-scoped gate가 열리고 최종 생성기가 모든 구간을 재검증한다.

이 범위에서 안전도, 서로 다른 여행지 추천 세 개, 실제 선택 버스의 당일 실시간 재판정까지
1차 완료로 판정한다. 전역 운영시간·verified 입구·전역 exact 시간표 확대는 후속 데이터
coverage 작업이다.
