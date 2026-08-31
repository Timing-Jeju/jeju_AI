# Codex MCP 라이브 재인수 (2026-08-31)

## 상태

상태: `MCP_SETUP_PASS / LIVE_TAGO_WINDOW_DEFERRED`

- 설정 병합 커밋: `0db01446184d8366d41ac86018837b65bf4e6c19`
- 라이브 복구 커밋: `ee2069f5e659107ef7d3a9d1478567d53298094f`
- 실행일: 2026-08-31 KST
- Codex 전역 등록: `jeju-day-trip-planner`, enabled local stdio, Env·인증 없음
- 동일 명령 직접 stdio: initialize·Pydantic Schema·여섯 도구 노출 성공

후속 라이브 인수에서는 장소 검색, 버스 전용 추천 세 개, confirmed 정류장, TMAP 이동
미리보기와 balanced Evaluate까지 성공했다. 실행 시각이 19시 활동창 이후라 TAGO를 호출하지
않았으므로 완전한 `MCP_LIVE_ACCEPTANCE_PASS`는 선언하지 않는다.

## 복구 근거

활성 데이터에는 후보 장소 20개와 버스 후보 구간 37,765개가 있었지만 자동 식사·카페를 끈
대표 요청은 전략별 해가 모두 0개였다. 연속 활동 상한만 완화한 비식별 진단에서는
`balanced` 51개, `relaxed` 19개, `experience_max` 70개의 해가 확인됐다. 생성기는 연속 활동
선호를 항상 하드 실패로 처리했지만 평가기는 좌석·실내 휴식이 필수가 아니면 주의사항으로
처리하던 불일치가 원인이었다.

First RED로 일반 휴식 선호와 필수 휴식 편의조건을 분리했다. 일반 요청은 평가 단계의 주의
판정을 허용하되 좌석 또는 실내 휴식이 필수인 요청은 기존 하드 실패를 유지한다. 또한
`inspect_jeju_bus_stop` 계약의 `route_numbers`가 저장소 조회에서 비어 있던 문제를 발견해,
선택 정류장이 실제로 포함된 활성 시간표와 활성 노선 publication에서 중복 제거된 노선번호를
반환하도록 수정했다.

## 도구별 라이브 상태

| 도구 | 상태 | 호출 수 | 안전한 결과 |
|---|---|---:|---|
| `search_jeju_places` | 성공 | 1 | 대상 숙소 1곳 유일 식별, source lineage 존재 |
| `recommend_jeju_day_trips` | 성공 | 1 | 추천 3개, `balanced`·`relaxed`·`experience_max`, transfer는 bus만 사용 |
| `inspect_jeju_bus_stop` | 성공 | 1 | `CONFIRMED`, provider mapping·노선번호·source lineage 존재 |
| `preview_jeju_transfer` | 성공 | 1 | taxi transfer, TMAP `driving_route`와 공식 택시요금 fact 결합 |
| `evaluate_jeju_day_trip` | `feasible_with_caution` | 1 | 4개 버스 segment 일치, `schedule_window_fit=true`, evidence verified |
| `revalidate_jeju_day_trip` | 보류 | 0 | 활동창 종료 뒤라 TAGO 호출 없이 `LIVE_TAGO_WINDOW_DEFERRED` |

최종 실행의 TMAP 결합은 성공했고 TAGO 결합은 시각 조건으로 보류했다. 추천과 평가의 모든
시간·거리·비용 참조는 응답의 evidence ledger에서 닫혔다. 세 추천 불변조건은 완화하지
않았으며 좌표·정류장/노선 ID·상세 경로·원본 body는 보고서에 기록하지 않았다.

## 보안·품질 게이트

- owner-only env와 런타임 세 변수 allowlist: `Pass`
- importer/migrator·TourAPI·OpenAI·S3/AWS·proxy·Python/Codex 변수 비전달: `Pass`
- TMAP·TAGO client 정상·예외 종료: `Pass`
- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- MCP 집중 테스트: `48 passed`
- 전체 offline pytest: `537 passed, 9 skipped`
- 격리 PostGIS·MinIO integration: `25 passed`
- Pydantic JSON Schema drift: `3 passed`
- synthetic example drift: `1 passed`
- checksum manifest: `1 passed`
- `uv lock --check`: `Pass`

격리 integration 뒤에는 test profile의 Postgres·MinIO만 중지했다. 개발 Postgres·MinIO는
중단하지 않았고 두 서비스의 healthy 상태를 다시 확인했다.

## 검증 공백과 잔여 위험

- 실행 시각이 19시 이후라 same-day 선택 버스에 대한 TAGO 실시간 도착 fact 결합은 보류됐다.
- 전역 exact 버스·운영시간·검증 입구 coverage는 별도 미완료 범위이며 1.0으로 과장하지 않는다.
- 새 세션의 여섯 도구 발견은 설정 병합 뒤 확인했지만, 라이브 복구 커밋 병합 후 main과 전역
  등록 경로의 최종 상태를 다시 확인해야 한다.

외부 경로 availability나 request-scoped 시간표 상태가 바뀌면 같은 명시적 인수 명령으로 다시
검증해야 한다. 완전한 `MCP_LIVE_ACCEPTANCE_PASS`는 검색, 정류장, TMAP preview, 서로 다른 버스
전용 추천 세 개, Evaluate, 선택 버스 TAGO 결합이 한 실행에서 모두 성공하기 전에는 선언하지
않는다. 인수 과정에서 좌표·상세 경로·API key·DSN·provider raw response·geometry·사용자 원문은
영속화하거나 보고서에 기록하지 않았다.
