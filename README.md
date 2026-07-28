# Timing Jeju FastAPI MCP

FastAPI MCP 서비스가 들어갈 위치입니다. 현재는 가져올 원본 저장소 URL과 브랜치가 제공되지 않아 가짜 구현을 만들지 않고 서비스 경계만 고정합니다.

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

구현 계약은 `docs/designs/timing-jeju-fastapi-mcp-contract.md`와 `docs/designs/timing-jeju-spring-fastapi-integration-contract.md`를 따릅니다.
