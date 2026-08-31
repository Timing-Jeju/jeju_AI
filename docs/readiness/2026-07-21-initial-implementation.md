# 제주 하루 여행 MCP 1차 readiness (역사 문서)

현재 상태는 `2026-08-10-v0.4-pre-data-handoff.md`를 따른다.
화장실 데이터는 이후 핵심 범위에서 제외됐으며 아래 언급은 당시 검증 공백 기록이다.

## 상태

`Partial`

코드·계약·로컬 저장 인프라·MCP stdio는 검증됐다. 실제 제주 동부권 성공 추천은 공식 시간표, TourAPI/TAGO 저장 허가, verified entrance, confirmed stop identity publication이 아직 없어 의도적으로 비활성 상태다. 현재 추천 도구는 값을 추정하지 않고 `insufficient_feasible_routes`를 반환한다.

## 최초 RED

- 성공 응답의 추천 수·전략·다양성 위반
- 버스 이동의 access/egress walk 누락
- 미승인 source의 네트워크 접근
- TMAP raw object 영속화와 과도한 cache TTL
- runtime 역할의 source_admin 접근
- 미지 evidence fact와 timeline totals 불일치
- 한글 docstring 누락

## 최소 GREEN

- v0.3.0 Pydantic 입력·출력과 생성 JSON Schema
- private/versioned MinIO raw 버킷과 PostGIS 4-schema/3-role migration
- append-only raw/publication/projection과 active read views
- source TOML allowlist·license·secret·quality 계약
- owner-only normalization spool과 publication transaction port
- TMAP process-memory TTL cache, safe metadata log, route adapter
- 정류장 mapping, service day, 도보/승차 안전시간, 세 전략 선택 검증
- LangGraph 책임 경계와 primary/fallback 모델 router
- FastMCP stdio 네 도구 및 구조화 성공/실패 응답

## 검증 근거

- `skill-creator quick_validate`: `Skill is valid!`
- 한글 테스트 설명 검사: Pass
- Ruff: Pass
- Pyright: 0 errors, 0 warnings
- Pytest offline: 46 passed, 2 live-only skipped
- Pytest with local PostGIS: 48 passed
- 실제 stdio subprocess: handshake, tools/list 4개, structuredContent 호출 통과
- PostGIS role check: importer raw UPDATE=false, runtime source_admin SELECT=false, runtime travel_read SELECT=true
- migration 재실행: no-op, 변경 checksum 거부 테스트 통과
- MinIO `jeju-private-raw`: anonymous none, versioning enabled

## 검증 공백

- TourAPI/TAGO/공휴일/화장실 live 수집과 실제 행 품질
- 제주 공식 미래 시간표의 source·라이선스·서비스데이 의미
- TMAP의 제주 미래 시각 semantics와 TAGO/제주 ID 연결
- 실제 동부권 entrance publication과 행정구역 polygon 품질 검사
- 실제 데이터로 생성된 세 경로의 현장 타당성
- 운영 환경의 S3 encryption, object lock/backup, secret manager

## 잔여 위험

소스 승인 없이 성공 응답을 활성화하면 운영시간·버스 시각·입구·정류장 방향을 사실처럼 오인할 수 있다. 따라서 `PENDING` source를 먼저 검토하고 raw/derivative 허가 evidence를 확정한 다음, importer publication과 실제 구간 PoC를 순서대로 진행해야 한다.
