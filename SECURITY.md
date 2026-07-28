# 보안 정책

## 비밀정보

`.env`, API 키, JWT 서명 키, 토큰, 인증서와 실제 사용자 개인정보를 커밋하지 않습니다. 로그와 테스트 fixture에도 실제 값을 사용하지 않습니다.

## 서비스 노출 범위

FastAPI MCP는 public ingress와 CORS를 열지 않습니다. `/mcp`, `/health/live`, `/health/ready`는 Spring 백엔드가 접근하는 private network에만 노출합니다. 사용자 JWT 대신 짧은 수명의 내부 service JWT만 검증합니다.

## 취약점 보고

보안 문제는 공개 Issue에 비밀정보나 재현 토큰을 첨부하지 말고 저장소 관리자에게 비공개로 전달합니다. 유출 가능성이 있으면 해당 자격 증명을 먼저 폐기·교체합니다.
