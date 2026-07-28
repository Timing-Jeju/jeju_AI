# Timing Jeju FastAPI MCP 에이전트 규칙

## 프로젝트와 명령

- 이 저장소는 [Spring 백엔드](https://github.com/Timing-Jeju/jeju_BE)가 private network로 호출하는 FastAPI MCP 서비스 전용이다.
- Python 3.12와 uv 잠금 파일을 사용한다.
- 의존성 설치: `uv sync --locked --group dev`
- 필수 품질 게이트: `./scripts/quality-gate.sh`
- 첫 기능 Issue 전에는 예시 Tool이나 가짜 계산 구현을 만들지 않는다.
- `app`, `src` 또는 특정 계층 구조를 강제하지 않는다. 첫 기능 Issue에서 테스트를 먼저 작성하며 패키지 구조를 함께 결정한다.

## 필수 개발 흐름

- 모든 변경은 GitHub Issue에서 시작한다. `main`과 `develop`에서 직접 개발·커밋·푸시하지 않는다.
- 최신 `develop`에서 `{type}/{issue-number}-{kebab-case-summary}` 브랜치를 만든다.
- 운영 코드보다 테스트를 먼저 작성하고 Red → Green → Refactor 증거를 남긴다.
- 작업 단위별 커밋 후 PR로 `develop`에 병합한다. 출시는 Release PR로 `develop`에서 `main`에 반영한다.
- 사용자에게 보이는 문서와 매 개발일 Obsidian `04_Projects/timing-jeju` 개발 일지는 한국어로 작성한다.

## 서비스 경계

- Supabase/PostgreSQL과 외부 TourAPI·TAGO·KMA·지도 API에 직접 접근하지 않는다.
- Spring이 제공한 구조화 facts만 계산하고 `structuredContent`로 결과를 반환한다.
- 사용자 JWT, refresh token, provider token과 개인정보를 입력으로 받지 않는다.
- 공개 ingress와 CORS를 열지 않고 `/mcp`, `/health/live`, `/health/ready`만 내부망에 제공한다.

## 완료 조건과 금지사항

- Ruff, mypy, pytest와 의존성 잠금 검사가 모두 통과해야 한다.
- 운영 Python 파일에는 대응 성공·실패·경계값 테스트를 추가한다.
- 실행 엔트리포인트를 정하는 첫 기능 Issue에서 coverage, Docker와 Health Check를 추가한다.
- `.env`, 키, 토큰, 인증서 등 비밀정보를 커밋하거나 로그에 출력하지 않는다.
- Hook 우회, force push, 파괴적 Git 명령과 자동 머지를 금지한다.
- Spring 계약이 바뀌면 [jeju_BE](https://github.com/Timing-Jeju/jeju_BE)에도 대응 Issue와 PR을 만들고 호환 순서를 합의한다.
