# 프로젝트 맥락

## 목표와 범위

제주 전역의 하루 일정 생성·정확 일정 사전 판정·여행 중 실시간 재판정을 로컬 stdio
MCP로 제공한다. 생성 성공 시 `balanced`, `relaxed`, `experience_max` 세 전략을 정확히
하나씩 반환한다. 사용자 일정·위치·원문은 서버에 영속 저장하지 않는다.

## 공개 도구

- `recommend_jeju_day_trips`: 구조화 조건으로 세 일정 생성 또는 전체 실패
- `evaluate_jeju_day_trip`: 활동-only 또는 전체 타임라인 원본 판정과 검증된 수정안
- `revalidate_jeju_day_trip`: 현재 진행상태와 선택적 GPS로 남은 일정 재판정
- `search_jeju_places`: 활성 장소 publication 검색
- `inspect_jeju_bus_stop`: 활성 정류장·노선·mapping 근거 조회
- `preview_jeju_transfer`: 입구-정류장-입구 연결과 단기 TMAP 결과 미리보기

`request_mode=generate`는 한 버전 호환하고 `improve`는 구조화 migration 오류로
`evaluate_jeju_day_trip` 사용을 안내한다.

## 기준 문서와 계층

- 제품 의도: `docs/PROJECT_PLAN.md`
- 공개 계약: `src/jeju_trip/domain/models.py`에서 생성한 `docs/contracts/*.json`
- 공식 소스 검토: `docs/OFFICIAL_SOURCES.md`, `config/data_sources.toml`
- 계층: domain → application/planning → infrastructure → interfaces. domain은 외부 SDK를 import하지 않는다.

유효 후보가 세 개 미만이거나 전략 간 실질적 다양성이 없으면 `insufficient_feasible_routes`다. 부분 성공은 없다.
