# JSON 계약 규칙

## 단일 원본

Pydantic 모델에서 MCP input/output Schema와 커밋된 JSON Schema를 생성한다. 수동
Schema를 별도로 유지하지 않는다. 계약 버전은 `0.6.0`이다.

## 성공과 실패

생성 성공은 정확히 세 추천과 서로 다른 세 전략을 요구한다. 실패는 추천을 비우고
구조화 `failure`, 모든 요청 장소의 `place_decisions`, capability coverage를 제공한다.
정확 일정은 `activities_only`와 `full_timeline`의 discriminated union이며 두 형식을
섞지 않는다. 실시간 결과는 완료 일정을 보존하고 남은 일정과 회복안만 별도로 반환한다.

## 이동과 근거

모든 버스 transfer는 `access_walk`와 `egress_walk`를 포함한다. 각 walk는 API expected, 속도 배수, 불확실성, planned 시간을 분리한다. 예정 버스 시각, 정류장 ID·좌표·방향, 택시 요금 범위, 운영시간과 계산 totals를 evidence fact에 연결한다.

`evidence_fact.derivation.input_fact_ids`는 존재하는 fact만 참조한다. 계산값은 공식 또는 외부 source fact와 정책 fact에서 파생되어야 한다. LLM이 추가한 미지 fact ID나 숫자는 최종 검증에서 거부한다.

## 시간 불변조건

모든 datetime은 `Asia/Seoul` offset을 포함한다. event duration은 end-start와 같고 겹치지 않는다. totals는 timeline 집계와 같아야 한다. 승차 정류장 도착은 예정 출발에서 boarding buffer를 뺀 시각보다 늦을 수 없다.
