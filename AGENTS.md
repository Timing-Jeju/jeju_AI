# 제주 하루 여행 MCP 불변조건

- Pydantic 모델을 공개 JSON 계약의 유일한 원본으로 사용한다.
- `config/data_sources.toml`에서 승인되지 않은 외부 데이터 소스를 호출하지 않는다.
- TMAP 원본 응답·상세 geometry·사용자 원문을 영속 저장하거나 로그로 남기지 않는다.
- LLM은 시간·거리·비용을 새로 만들 수 없으며 기존 evidence fact ID만 참조한다.
- 모든 Python 테스트에는 한글 목적을 설명하는 docstring이 있어야 한다.
- 서로 다른 유효 추천 세 개를 만들 수 없으면 부분 성공 없이 `insufficient_feasible_routes`를 반환한다.
