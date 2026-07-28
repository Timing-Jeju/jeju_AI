# Timing Jeju FastAPI MCP

Timing Jeju의 일정 계산과 AI 기능을 담당하는 내부 FastAPI MCP 저장소입니다. Python 버전, 의존성 잠금과 품질 도구만 제공하며 애플리케이션 패키지 구조는 아직 정하지 않습니다.

공개 REST API, 사용자 인증, DB·외부 API 연동과 계산 결과 저장은 별도 [Timing-Jeju/jeju_BE](https://github.com/Timing-Jeju/jeju_BE) Spring 백엔드가 담당합니다. 이 서비스는 Spring이 private network로 전달한 구조화 facts만 계산합니다.

## 준비된 최소 환경

- Python 3.12
- uv 기반 의존성 및 `uv.lock`
- FastAPI와 MCP Python SDK 안정 버전 1.x
- Ruff 린트·포맷, mypy 엄격 타입 검사, pytest
- develop/main 대상 독립 GitHub Actions CI
- 운영 Python 파일이 생기면 대응 테스트를 요구하는 품질 게이트

```bash
uv sync --locked --group dev
./scripts/quality-gate.sh
```

uv가 없다면 [uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)에 따라 먼저 설치합니다.

## 개발자가 결정할 내용

첫 FastAPI 기능 Issue에서 서비스 특성과 팀 합의에 따라 패키지 루트, 모듈 분리, 테스트 디렉터리와 실행 엔트리포인트를 결정합니다. `app/`, `src/`, 도메인·계층 디렉터리 중 어느 것도 현재 설정이 강제하지 않습니다.

실행 엔트리포인트가 정해진 뒤 Dockerfile, Health Check와 커버리지 대상을 추가합니다. 구조를 정하기 전에는 빈 엔드포인트나 예시 MCP Tool을 만들지 않습니다.

## 책임

- Spring API가 전달한 구조화 facts 기반 일정 생성·검증·위험도 계산
- Stateless MCP Streamable HTTP `POST /mcp`
- 내부용 `/health/live`, `/health/ready`
- 결과의 `contractVersion`, `inputHash`, reason code 유지

## 금지 사항

- DB 직접 접근
- 외부 관광·교통·날씨·지도 API 직접 호출
- 입력에 없는 ID 생성
- 사용자 JWT와 개인정보 수신
- 활성 일정 직접 변경

## 계약 문서

- [FastAPI MCP 구현 계약](docs/FASTAPI_MCP_CONTRACT.md)
- [Spring-FastAPI 내부 연동 명세](https://github.com/Timing-Jeju/jeju_BE/blob/develop/docs/designs/timing-jeju-spring-fastapi-integration-contract.md)

기여 흐름은 [기여 가이드](CONTRIBUTING.md), 비밀정보와 보안 보고는 [보안 정책](SECURITY.md)을 따릅니다.
