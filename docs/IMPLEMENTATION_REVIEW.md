# v0.2 구현 리뷰 (역사 문서)

이 문서는 v0.2 당시의 역사적 리뷰다. 현재 공개 계약은 v0.4.0이며
`src/jeju_trip/domain/models.py`, `docs/PROJECT_PLAN.md`, `docs/JSON_CONTRACT.md`가 우선한다.
화장실과 TMAP 대중교통은 현재 엔진의 비핵심 데이터로 판정되어 수집·publication 범위에서
제외됐다. 아래 관련 내용은 당시 검토 기록일 뿐 현재 구현 지침이 아니다.

## 결론

v0.1의 방향은 맞지만 그대로 구현하면 LLM이 만든 그럴듯한 시각과 외부 API의 실제 근거를 구분하기 어렵고, 버스 정류장 앞뒤의 보행이 누락되며, 미래 계획에서 좌석·화장실이 보장되는 것처럼 보일 수 있다. v0.2는 계획 스냅샷, 문에서 문까지의 이동, 안전시간의 분해, 편의시설 증거 모델을 추가한다.

## 반드시 수정한 설계

1. 실시간 중심 필드를 사전계획 중심으로 변경한다.
   - `planning_context`, `schedule_basis`, `plan_expires_at`, `refresh_policy`를 최상위에 둔다.
   - 미래 일정에서는 `realtime_*`를 기본적으로 `null`로 두고 계획 점수에 사용하지 않는다.
2. 버스 이동을 하나의 소요시간으로 취급하지 않는다.
   - 출발 준비 → 정류장 접근 도보 → 승차 안전시간/대기 → 버스 → 환승 → 하차 후 도보 순으로 계산한다.
3. 예상시간과 계획시간을 분리한다.
   - API 예상 도보 8분을 그대로 계획에 쓰지 않고 사용자 속도와 경로 불확실성을 더한 계획시간을 사용한다.
4. 편의시설을 증거 기반으로 표현한다.
   - 단순 `true/false` 대신 `requirement`, `availability`, `guarantee`, `confidence`, `source_refs`를 둔다.
5. LLM 최종 JSON을 그대로 반환하지 않는다.
   - LangGraph 상태의 정규화된 객체를 Pydantic 모델로 조립하고 결정론적 검증기 통과 후 직렬화한다.

## 권장 모듈 구조

```text
src/
  mcp_server/
    server.py
    tools/recommend_day_trips.py
  domain/
    request_models.py
    itinerary_models.py
    provenance_models.py
  agents/
    graph.py
    request_parser.py
    candidate_generator.py
    explanation_writer.py
  adapters/
    tourapi.py
    tmap_transit.py
    tmap_routes.py
    tago.py
    holiday_calendar.py
    public_restroom.py
  planning/
    place_resolver.py
    route_matrix.py
    scheduler.py
    safety_margin.py
    rest_policy.py
    scorer.py
    diversity.py
  validation/
    schema_validator.py
    timeline_validator.py
    transit_validator.py
    walking_validator.py
    amenity_validator.py
    provenance_validator.py
```

## LangGraph 권장 노드

```text
parse_request
  → resolve_service_day
  → resolve_places_and_entrances
  → fetch_opening_hours
  → expand_visit_meal_rest_candidates
  → fetch_access_and_egress_walks
  → fetch_future_transit_routes
  → generate_candidate_orders
  → schedule_with_safety_margins
  → validate_candidates
  → repair_or_drop_invalid_candidates
  → select_three_diverse_routes
  → write_reasons
  → final_deterministic_validation
```

LLM은 `generate_candidate_orders`, `repair_or_drop_invalid_candidates`, `write_reasons`에 집중한다. 시각·합계·안전시간·출처는 코드가 만든다.

## 계획용 안전시간 구현

안전시간 계산기는 입력 근거를 보존해야 한다.

```python
planned_walk = ceil(expected_walk * speed_multiplier) + route_uncertainty
recommended_stop_arrival = scheduled_departure - boarding_buffer
latest_safe_origin_departure = (
    recommended_stop_arrival
    - planned_walk
    - departure_preparation
)
```

`speed_multiplier`, `route_uncertainty`, `boarding_buffer`, `departure_preparation`은 각각 JSON에 반환한다. 막차와 저빈도 버스는 별도 위험 정책을 적용하며 단순히 점수만 낮추지 말고 이전 편을 우선 선택한다.

## 좌석·화장실·카페 판단

- 좌석: 시설 존재와 당일 빈 좌석은 다른 사실이다. 미래 계획에서는 `availability: likely|unknown`, `guarantee: not_guaranteed`만 가능하다. 고령자·이동약자에게 좌석이 필수라면 빈 좌석을 가정하지 않고 예약 가능한 장소나 충분한 대체 휴식 후보를 선택한다.
- 화장실: 2시간 이상의 연속 구간 전후에 후보를 확인한다. 행정안전부 표준데이터는 2025년 2월부터 좌표가 제외되었으므로 주소 재지오코딩 결과와 원본 주소를 함께 보존한다.
- 카페: 운영시간을 검증하고 휴식 후보로 쓸 수 있지만 미래 좌석과 대기시간은 보장하지 않는다. 반드시 주변 대체 후보를 둔다.
- 버스 좌석: 일반 버스는 좌석 보장이 없으므로 기본 휴식시간으로 계산하지 않는다.

## 구현 순서

1. Pydantic 요청·응답 모델과 v0.2 JSON Schema
2. 시간·도보·버스 안전시간 검증기
3. 고정 fixture 기반 일정 스케줄러
4. TourAPI/TMAP/TAGO 어댑터
5. LangGraph 후보 생성과 수선 루프
6. MCP 공개 도구
7. 제주 실제 구간 계약 테스트

LLM 연동보다 1~3단계를 먼저 완성해야 외부 데이터와 모델의 오류를 분리해서 테스트할 수 있다.

## 승인 기준

- 성공 응답에 유효하고 서로 다른 추천 3개가 존재한다.
- 모든 버스 구간에 access/egress walk가 있다.
- 모든 walk는 expected/planned/safety 시간이 구분된다.
- 모든 버스 탑승에 recommended stop arrival와 latest safe origin departure가 있다.
- 실시간 데이터 없이도 미래 일정이 생성된다.
- 모든 편의시설 주장에 출처·신뢰도·보장 여부가 있다.
- 합계, 이벤트 순서, 운영시간, 사용자 제약, Schema가 모두 통과한다.
