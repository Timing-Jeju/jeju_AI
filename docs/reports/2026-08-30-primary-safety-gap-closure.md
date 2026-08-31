# 2026-08-30 1차 안전 공백 보완

## 상태

코드·계약·격리 통합 검증은 `Pass`, 최신 운영 데이터와 외부 API 종단 인수는 `Deferred`다.
stale snapshot은 더 이상 정상 준비 상태나 `ACTIVE` 근거로 공개되지 않으므로, 현재 운영
데이터가 갱신되기 전에는 성공을 가장하지 않고 readiness가 닫힌다.

## 변경 범위

1. `config/data_sources.toml`의 `SOURCE_DATE`, `OBSERVED_AT`, `RETRIEVED_AT`과 source별
   `freshness_days`를 공통 판정으로 구현했다.
2. active coverage가 1이어도 연결된 source가 stale이면 capability를 `false`로 닫는다.
   장소 해석·후보·대표좌표·이동 preview도 stale 장소를 사용하지 않는다.
3. Generate·Evaluate·Revalidate·transfer preview의 evidence 참조를 응답 내부 fact ledger와
   대조한다. totals, place decision, segment/activity/issue, repair/recovery와 computed fact의
   derivation input도 미지 ID를 허용하지 않는다.
4. source URL을 반복 percent-decoding한 뒤 dot-segment·backslash·비표준 HTTPS port를
   거부하고 정규화된 path segment 경계로 allowlist를 검사한다.
5. PR과 main push에 offline 품질 게이트와 tmpfs 기반 PostGIS·MinIO 통합 테스트를 추가했다.
6. README의 설치·MCP 실행·격리 테스트·freshness·privacy runbook과 skill의 계약 버전을
   v0.6.0 기준으로 갱신했다.

## First RED와 최소 GREEN

- stale 장소 coverage는 기존 구현에서 `true`, source metadata는 `ACTIVE`였다. 공통 temporal
  판정과 readiness 집계를 적용한 뒤 각각 `false`, `STALE`이 됐다.
- 존재하지 않는 totals·place decision·evaluation segment·recovery fact ID가 기존
  Pydantic 검증을 통과했다. 공통 closure validator 적용 뒤 모두 계약 오류가 된다.
- `/B551011/KorService2/../unapproved`, percent/double-percent 변형과 port 444 요청이 기존
  prefix 검사를 통과했다. 정규화 전 위험 표현을 거부한 뒤 모두 네트워크 전에 차단된다.
- CI 파일이 없어 계약 테스트가 실패했다. 필수 명령 순서와 격리 endpoint를 선언한 workflow를
  추가한 뒤 통과했다.

## 검증 근거

- 한글 테스트 목적 설명: Pass
- Ruff: Pass
- Pyright: Pass
- offline pytest: 451 passed, 9 live-environment skipped
- 격리 PostGIS·MinIO integration: 24 passed, 0 skipped
- 기존 개발 PostGIS·MinIO: 검증 전후 모두 healthy
- 임시 test 컨테이너와 tmpfs 데이터: 검증 직후 제거

## 현재 운영 데이터 판정

2026-08-30 현재 local active DB를 새 freshness gate로 읽으면 다음 capability가 닫힌다.

- `place_search_ready=false`: `tourapi.place` 관측본이 8일 계약을 초과
- `service_area_ready=false`: 활성 경계 source date가 370일 계약을 초과
- `fare_policy_ready=false`: 결합된 요금 source 중 하나가 계약을 초과
- `opening_hours_ready=false`: exact coverage가 1 미만

버스 노선·route-stop·confirmed mapping과 운영시간 snapshot 자체의 fresh coverage는 유지된다.
이 판정은 availability 저하가 아니라 오래된 사실로 성공하지 않기 위한 의도한 fail-closed다.

## 검증 공백

현재 실행 환경과 저장소 내부에 `JEJU_TMAP_API_KEY`, `JEJU_TAGO_SERVICE_KEY`,
`JEJU_TOURAPI_SERVICE_KEY`, `JEJU_RUNTIME_DSN`이 없으므로 최신 active DB를 사용한 외부
Generate→Evaluate→Revalidate 인수는 실행하지 않았다. stale 정적 source의 새 raw-first
publication도 해당 공식 원본과 자격증명이 준비된 뒤 별도 수행해야 한다.

개발 DB의 과거 비활성 `integration.*` 등록은 append-only 감사 이력이므로 삭제하지 않았다.
활성 snapshot은 없으며, 테스트 전용 DB 이름과 MinIO endpoint guard 및 CI 격리로 재발 경로를
차단했다.

## 잔여 위험

- GitHub repository settings에서 workflow를 required status check로 지정하기 전까지는 관리자
  설정에 따라 CI를 우회한 merge가 가능하다.
- 외부 API live 인수와 stale source 재발행 전까지 실제 추천 availability는 승인하지 않는다.
- exact 운영시간과 버스 route pattern의 전역 coverage가 1이 아니므로 기존의 부분 coverage
  경고·요청 단위 버스 gate를 유지한다.
