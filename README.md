# 제주 하루 여행 MCP

공식·승인 데이터와 결정론적 검증만으로 제주 하루 일정을 생성하고 평가하는 로컬 stdio
MCP 서버입니다. 공개 계약 버전은 `0.7.0`이며, 성공 시 `balanced`, `relaxed`,
`experience_max` 일정 세 개를 모두 반환합니다. 세 개를 만들 수 없으면 부분 결과 없이
`insufficient_feasible_routes`로 종료합니다.

## 요구사항

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/)
- 격리 통합 테스트 또는 로컬 publication 작업 시 Docker와 Docker Compose
- 외부 API live 실행 시 승인 source에 대응하는 개별 API key

## 설치와 기본 검증

```bash
uv sync --locked
uv run python scripts/check_korean_test_descriptions.py
uv run ruff check .
uv run pyright
uv run pytest
```

동일한 순서의 offline gate와 격리 PostGIS·MinIO 테스트가 GitHub Actions에서 PR과 `main`
push마다 실행됩니다.

## 로컬 MCP 실행

기존 `jeju-trip-mcp`는 개발자가 환경을 직접 관리하는 수동 실행점입니다. 운영용 read-only
DSN과 필요한 API key를 현재 프로세스 환경에만 설정하며, 저장소나 로그에 실제 값을 기록하지
않습니다.

```bash
export JEJU_RUNTIME_DSN='postgresql://jeju_runtime:...@127.0.0.1:55432/jeju_trip'
export JEJU_TMAP_API_KEY='...'
export JEJU_TAGO_SERVICE_KEY='...'
uv run jeju-trip-mcp
```

Codex에는 수동 실행점 대신 `jeju-trip-mcp-codex` 보안 실행점을 등록합니다. 이 실행점은
저장소 루트의 `.env` symlink를 최종 대상까지 해석한 뒤 regular file, 현재 사용자 소유,
group/other 권한 없음, 64 KiB 이하, UTF-8 조건을 모두 검사합니다. 기본 파일을 바꾸는
`JEJU_MCP_ENV_FILE`은 테스트·별도 배포에만 사용하며 MCP 서버 환경에는 남기지 않습니다.

파일에서 런타임으로 전달하는 값은 다음 세 개뿐입니다.

- `JEJU_RUNTIME_DSN`
- `JEJU_TMAP_API_KEY`
- `JEJU_TAGO_SERVICE_KEY`

importer/migrator DSN, TourAPI·Holiday, OpenAI, S3/AWS, proxy, `PYTHONPATH`, Codex 내부 변수와
그 밖의 `JEJU_*` 값은 전달하지 않습니다. `JEJU_RUNTIME_DSN`이 importer, migrator 또는
관리자 성격 역할을 가리키거나 필수 세 값이 비어 있으면 종료 코드 `78`로 fail-closed 합니다.
dotenv는 shell로 실행하지 않으며 변수·명령 치환 문법을 거부합니다.

## Private Streamable HTTP MCP

Spring worker 연동은 `jeju-trip-mcp-http` 실행점의 stateless JSON Streamable HTTP `/mcp`를
사용합니다. stdio와 HTTP는 같은 여섯 도구와 schema checksum을 노출합니다. HTTP launcher는
owner-only `.env.http`에서 runtime 세 값과 bind, issuer, audience, local JWKS, TLS 인증서·키
경로만 선택합니다. 최대 5분 RS256 JWT에 `jeju:mcp:invoke` scope와 JTI가 필요합니다.

기본 bind는 `127.0.0.1:8000`입니다. 컨테이너에서 `0.0.0.0`을 사용할 때는 public port를
publish하지 않은 private network임을 확인하고 `JEJU_MCP_PRIVATE_NETWORK_CONFIRMED=true`를
명시해야 합니다. `/health`와 `/ready`는 인증 정보나 provider 상태 원문을 반환하지 않습니다.
배포 예시는 `infra/compose.private-http.yml`, 운영 상세 계약은
[`FASTAPI_MCP_CONTRACT.md`](docs/FASTAPI_MCP_CONTRACT.md)를 참고합니다.

### Codex 등록과 롤백

코드가 `main`에 병합된 뒤 다음처럼 local stdio MCP를 등록합니다. `--env`나
`uv --env-file`을 사용하지 않으므로 Codex 전역 설정에 secret이 저장되지 않습니다.

```bash
REPOSITORY_DIR=/absolute/path/to/jeju_AI

codex mcp add jeju-day-trip-planner -- \
  /opt/homebrew/bin/uv \
  --directory "$REPOSITORY_DIR" \
  run --frozen --no-env-file \
  jeju-trip-mcp-codex

codex mcp get jeju-day-trip-planner --json
codex mcp list
```

등록 결과의 command, directory, `--frozen`, `--no-env-file`, 빈 Env, enabled local stdio 상태를
확인한 뒤 Codex를 재시작하거나 새 세션을 열어 여섯 도구를 다시 발견해야 합니다. 등록만
되돌릴 때는 코드나 DB를 변경하지 않고 다음 명령을 사용합니다.

```bash
codex mcp remove jeju-day-trip-planner
```

### 라이브 MCP 인수

기본 pytest와 CI는 외부 API를 호출하지 않습니다. 실제 KST 당일 DB·TMAP·TAGO를 검증할 때만
다음 명시적 명령을 사용합니다.

```bash
uv run --frozen --no-env-file python scripts/run_live_mcp_acceptance.py --confirm-live
```

`MCP_LIVE_ACCEPTANCE_PASS`는 장소 검색, confirmed 정류장, TMAP 미리보기, 서로 다른 버스 전용
추천 세 개, Evaluate, 선택 버스의 same-day TAGO fact 결합까지 모두 통과했다는 뜻입니다.
TAGO 운행 종료나 현재 도착정보 부재 시 최대 세 stop/route만 확인하고
`MCP_SETUP_PASS / LIVE_TAGO_WINDOW_DEFERRED`로 기록합니다. 세 추천을 만들 수 없으면 추천을
비우고 `insufficient_feasible_routes`를 유지하며 live availability 실패를 성공으로 바꾸지
않습니다.

인수 출력과 보고서에는 상태, 도구명, 추천 수, 전략, transfer mode, fact category, 호출 수만
남깁니다. 좌표·상세 경로·정류장/노선 ID·API key·DSN·provider raw response·geometry·사용자
원문은 기록하지 않습니다.

`JEJU_RUNTIME_DSN` 또는 경로 adapter가 없으면 도구는 사실을 추정하지 않고 구조화된 데이터
부족 응답을 반환합니다. 검색·정류장 조회·생성·판정·재판정·이동 미리보기는 모두
`config/data_sources.toml`의 source별 freshness를 통과한 활성 snapshot만 사용합니다.
버스와 택시 요금 readiness는 서로 분리되어 한 수단의 stale 정책이 다른 수단 전용 요청을
막지 않습니다.

## 격리 통합 테스트

개발 DB와 MinIO를 테스트에 사용하지 않습니다. `test` profile은 tmpfs 기반
`jeju_trip_test`와 별도 MinIO endpoint만 엽니다.

```bash
docker compose -f infra/compose.local.yml --profile test up -d postgres-test minio-test

JEJU_TEST_ADMIN_DSN='postgresql://postgres:local-postgres-test-only@127.0.0.1:55433/jeju_trip_test' \
JEJU_TEST_S3_ENDPOINT='http://127.0.0.1:59010' \
JEJU_TEST_S3_ACCESS_KEY='jeju-test-admin' \
JEJU_TEST_S3_SECRET_KEY='jeju-test-password-only' \
uv run pytest tests/integration -q -rs

docker compose -f infra/compose.local.yml --profile test down
```

테스트 fixture는 DSN의 DB 이름이 `jeju_trip_test`가 아니거나 MinIO endpoint가
`127.0.0.1:59010`이 아니면 시작 자체를 거부합니다.

## 데이터와 보안 경계

- 공개 JSON Schema의 유일한 원본은 `src/jeju_trip/domain/models.py`입니다.
- 외부 네트워크는 `config/data_sources.toml`의 승인 상태·HTTPS host·정규화 path·응답 크기
  제한을 모두 통과해야 합니다.
- TMAP/TAGO 원문, TMAP 상세 geometry, 사용자 원문·일정·GPS는 영속 저장하거나 로그로
  남기지 않습니다.
- 시간·거리·비용 및 계산값은 응답 내부 evidence ledger에 존재하는 fact ID로 완전히
  추적돼야 하며, 모든 `SourceRef.source_id`는 같은 응답의 `data_sources`에 존재해야 합니다.
- 활성 snapshot이 freshness 계약을 넘으면 readiness를 닫고 source metadata를 `STALE`로
  표시합니다.

제품 범위와 공개 계약은 [PROJECT_PLAN.md](docs/PROJECT_PLAN.md)와
[JSON_CONTRACT.md](docs/JSON_CONTRACT.md)를 참고합니다. 운영 데이터 활성화·외부 live 인수는
`docs/reports`의 최신 날짜 보고서와 함께 확인합니다.
