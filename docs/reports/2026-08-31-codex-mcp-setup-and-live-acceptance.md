# Codex MCP 보안 세팅·라이브 인수 (2026-08-31)

## 상태

상태: `MCP_SETUP_PREMERGE_PASS / MCP_LIVE_ACCEPTANCE_FAILED`

- 브랜치: `feat/complete-codex-mcp-setup-20260831`
- 라이브 실행 기준 커밋: `4901663f563d1fa8085db260568898cf16cb132b`
- 실행일: 2026-08-31 KST
- Codex 전역 등록: 병합 후 변경 원칙에 따라 아직 미등록
- 동일 명령 직접 stdio: initialize·Schema·여섯 도구 노출 성공

보안 실행점, 비밀값 경계, client 종료, stdio와 Pydantic Schema 회귀, 전체 offline 및 격리
integration은 통과했다. 실제 장소 검색은 성공했지만 버스 전용 추천은 서로 다른 유효 경로
세 개를 만들지 못해 추천 0개와 `insufficient_feasible_routes`로 닫혔다. 따라서 장소 검색
이후의 정류장·TMAP 미리보기·Evaluate·same-day TAGO Revalidate를 라이브 성공으로 주장하지
않는다.

## 구현·보안 검증 근거

- `jeju-trip-mcp-codex`는 최종 env 대상이 regular file, 현재 사용자 소유, group/other 권한
  없음, 64 KiB 이하, UTF-8인지 open 전후 stat으로 검사한다.
- 런타임에는 `JEJU_RUNTIME_DSN`, `JEJU_TMAP_API_KEY`, `JEJU_TAGO_SERVICE_KEY`만 전달한다.
- importer/migrator, TourAPI·Holiday, OpenAI, S3/AWS, proxy, Python 주입, Codex 내부 환경은
  제거한다.
- dotenv를 shell로 실행하지 않고 변수·명령 치환과 관리자 성격 runtime 역할을 거부한다.
- TMAP·TAGO client는 키가 있을 때만 만들고 정상 종료와 초기화 예외 모두에서 한 번 닫는다.
- secret fixture를 포함한 런처 오류와 stdio stderr에 secret·traceback이 없음을 검사했다.
- 여섯 MCP input/output Schema는 Pydantic 생성 Schema와 정확히 일치했다.

## 도구별 라이브 상태

| 도구 | 상태 | 안전한 결과 |
|---|---|---|
| `search_jeju_places` | 성공 | 대상 숙소 1곳 유일 식별, source lineage 존재 |
| `recommend_jeju_day_trips` | 전체 실패 | 추천 0개, `insufficient_feasible_routes` |
| `inspect_jeju_bus_stop` | 미실행 | 선택 버스 추천이 없어 호출하지 않음 |
| `preview_jeju_transfer` | 미실행 | 생성 실패 뒤 외부 호출을 확대하지 않음 |
| `evaluate_jeju_day_trip` | 미실행 | 변환할 balanced 추천이 없음 |
| `revalidate_jeju_day_trip` | 미실행 | 선택 버스가 없고 실행 시각도 운행 종료 후임 |

명시적 인수 실행 1회와 원인 분류용 안전 진단 2회에서 장소 검색과 추천을 각각 3회
호출했다. 세 추천 불변조건은 완화하지 않았으며, 최종 reason code는
`BUS_ONLY_STRATEGY_ROUTE_UNAVAILABLE`이었다. TMAP 또는 TAGO fact의 성공 결합은 확인되지
않았다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- MCP 집중 테스트: `48 passed`
- 전체 offline pytest: `535 passed, 9 skipped`
- 격리 PostGIS·MinIO integration: `25 passed`
- Pydantic JSON Schema drift: `3 passed`
- synthetic example drift: `1 passed`
- checksum manifest: `1 passed`
- `uv lock --check`: `Pass`

격리 compose 종료가 기존 개발 Postgres·MinIO 컨테이너도 함께 중지한 것을 확인해, volume을
삭제하지 않은 상태에서 두 개발 서비스를 즉시 다시 기동했다.

## 검증 공백

- Codex 전역 등록과 새 Codex 세션의 실제 여섯 도구 발견은 코드가 `main`에 병합된 뒤에만
  수행한다.
- 라이브 추천 세 개가 없으므로 confirmed 정류장, TMAP 이동 미리보기, balanced Evaluate는
  이번 실행에서 확인하지 못했다.
- 실행 시각이 19시 이후였고 선택 버스도 없어 same-day TAGO fact 결합을 확인하지 못했다.
- 전역 exact 버스·운영시간·검증 입구 coverage는 별도 미완료 범위이며 1.0으로 과장하지 않는다.

## 잔여 위험

외부 경로 availability나 request-scoped 시간표 상태가 바뀌면 같은 명시적 인수 명령으로 다시
검증해야 한다. 완전한 `MCP_LIVE_ACCEPTANCE_PASS`는 검색, 정류장, TMAP preview, 서로 다른 버스
전용 추천 세 개, Evaluate, 선택 버스 TAGO 결합이 한 실행에서 모두 성공하기 전에는 선언하지
않는다. 인수 과정에서 좌표·상세 경로·API key·DSN·provider raw response·geometry·사용자 원문은
보고서에 기록하지 않았다.
