# AI #17 저장 projection 기반

## 승인 사실과 범위

사용자가 2026-09-13 작업 대화에서 TMAP 파생값 저장에 대한 서면 답변 및 전화 확인이
있었다고 알리고 해당 개발을 명시 승인했다. 에이전트가 공급자 계약 원문을 검토하거나
계약 번호·검토자·보존 기간을 확인했다고 주장하지 않는다.

원본 응답·상세 geometry·사용자 원문 저장 금지는 유지한다. 기존 TMAP raw cache의
TTL과 source manifest는 이 기반 변경으로 확대하지 않는다. 새 DTO는 저장용 변환 경계를
정의할 뿐 source manifest의 모든 내부 파생값 저장을 일괄 허용하지 않는다.

## 구현

- `DurableCandidateSet` Pydantic 모델과 생성 JSON Schema.
- 검증된 DayTripResponse를 다시 검증한 뒤 세 전략을 명시적 필드만 복사.
- 이벤트 시각/기간/거리, 합계, 점수, 위험, fact/source ID 및 계산 입력 ID 보존.
- evidence의 Any value, 원본 request, 좌표 포함 transfer 본문, 설명 원문은 복사하지 않음.
- unknown field, 부분 후보, 끊긴 fact 참조, 중복 이벤트 및 겹친 시각 거부.
- 기존 plan expiry를 그대로 사용하며 저장 가능성과 freshness를 혼동하지 않음.

## 검증과 미완료

First RED: 변환기 import 실패. GREEN: 신규 테스트 4개 및 대상 Ruff 통과.
독립 리뷰로 시간대/만료 순서·유형별 합계·중복 경로·근거 순환 검증을 추가했다.
각 오염 사례의 RED 4건을 확인한 뒤 GREEN으로 수정했다. 기존 추천 모델과 저장 DTO는
동일한 결정론적 다양성 함수를 사용하며, 체류시간만 다른 정상 후보의 회귀도 포함한다.
최종 테스트는 582 passed / 9 skipped, 한글 설명·Ruff·Pyright 통과다.
근거 ID의 참조와 순환을 검사하지만, provenance에는 실제 fact 값이 없으므로 이것만으로
원천 데이터의 진실성이나 재평가 가능성을 보증하지 않는다.

이 DTO는 아직 MCP 도구 응답에 추가되지 않았고 BE DB writer도 소비하지 않는다.
정류장별 이동 상세, 구간별 요금, 근거 값 재평가, previous_days 재조립,
후보 24시간 수명 계약 및 워커/조회/원자적 적용은 후속 구현이 필요하다.
그러므로 이 변경만으로 전체 연결이나 재시작 복구를 완료했다고 볼 수 없다.
