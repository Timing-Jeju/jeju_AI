# 기여 가이드

모든 변경은 GitHub Issue에서 시작합니다. 최신 `develop`에서 `{type}/{issue-number}-{kebab-case-summary}` 브랜치를 만들고 테스트를 먼저 작성해 Red → Green → Refactor 순서로 개발합니다.

```bash
uv sync --locked --group dev
./scripts/quality-gate.sh
```

애플리케이션 패키지 구조는 첫 기능 Issue에서 팀이 결정합니다. 구조 합의 전에는 예시 Tool, 빈 엔드포인트나 가짜 계산 코드를 추가하지 않습니다.

Spring과 FastAPI 계약을 함께 변경할 때는 [Timing-Jeju/jeju_BE](https://github.com/Timing-Jeju/jeju_BE)에 대응 Issue와 PR을 만들고 배포 호환 순서를 기록합니다. 구현 계약은 [FastAPI MCP 구현 계약](docs/FASTAPI_MCP_CONTRACT.md)을 따릅니다.

작업 단위별 커밋을 만들고 품질 게이트가 통과하면 `develop` 대상 PR을 생성합니다. `main`·`develop` 직접 push, force push, Hook 우회와 자동 머지는 금지합니다.
