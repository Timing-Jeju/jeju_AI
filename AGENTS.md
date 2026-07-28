# FastAPI MCP 에이전트 규칙

## 적용 범위

- 이 디렉터리는 Spring API가 private network로 호출하는 FastAPI MCP 서비스 전용이다.
- 저장소 루트 `AGENTS.md`의 Issue·Git·리뷰·보안 규칙을 모두 상속한다.
- 첫 기능 Issue 전에는 예시 Tool이나 가짜 계산 구현을 만들지 않는다.
- `app`, `src` 또는 특정 계층 구조를 강제하지 않는다. 첫 기능 Issue에서 테스트를 먼저 작성하며 패키지 구조를 함께 결정한다.

## 서비스 경계

- Supabase/PostgreSQL과 외부 TourAPI·TAGO·KMA·지도 API에 직접 접근하지 않는다.
- Spring이 제공한 구조화 facts만 계산하고 `structuredContent`로 결과를 반환한다.
- 사용자 JWT, refresh token, provider token과 개인정보를 입력으로 받지 않는다.
- 공개 ingress와 CORS를 열지 않고 `/mcp`, `/health/live`, `/health/ready`만 내부망에 제공한다.

## 품질 기준

- Python 3.12와 `uv.lock`을 기준으로 `./scripts/quality-gate.sh`를 실행한다.
- Ruff, mypy와 pytest를 기본 품질 도구로 사용한다.
- 운영 Python 파일을 추가할 때는 대응 테스트 파일을 함께 추가한다.
- 실행 엔트리포인트가 정해지는 첫 기능 Issue에서 coverage 대상과 Docker Health Check를 추가한다.
- 모든 계산 변경은 성공·실패·경계값 테스트를 먼저 작성해 Red → Green → Refactor로 개발한다.
