# 제주 전역·다일 여행 JSON 계약 v0.7.0

## 단일 원본

공개 입력·출력 계약의 원본은 `src/jeju_trip/domain/models.py`의 Pydantic 모델이다. `scripts/generate_json_schemas.py`가 `docs/contracts` 파일을 생성한다. 생성 Schema와 커밋된 파일이 다르면 테스트가 실패한다.

모든 datetime은 timezone offset을 포함한 RFC 3339 문자열, 기간은 분, 거리는 미터, 금액은 원 단위다. 여행 timezone은 `Asia/Seoul`이다.

## 공통 여행 조건

생성·정확 일정 판정은 `trip_date`, `timezone`, `accommodation`, `activity_window`,
`day_boundary`, `place_duration_preferences`, `party`, `transport`, `walking`, `rest`를 공유한다.
timezone은 `Asia/Seoul`만 허용하고 모든 datetime은 `+09:00`이어야 한다.
활동 시작일은 `trip_date`와 같고 활동 창은 24시간 이하여야 한다.

`day_boundary.start_place/end_place`가 하루 장소 경계다. 생략했을 때만 accommodation을
양쪽 endpoint로 사용한다. 검증 입구가 없으면 active TourAPI 장소 fact의 대표좌표를
사용하며 `REPRESENTATIVE_PLACE_POINT`로 표시한다. 이름만 제공된 숙소·terminal·장소를
하나로 확정할 수 없으면 추정하지 않고 `PLACE_AMBIGUOUS`로 중단한다.

`place_duration_preferences`는 `{place_id, requested_stay_minutes}`의 중복 없는 배열이다.
사용자 값은 전략 차별화나 자동 repair 과정에서 축소할 수 없다. 이 값 때문에 일정이 맞지
않으면 후보를 실패시키거나 evidence로 닫힌 repair option만 제안한다.

## 추천 입력

`RecommendDayTripsInput`은 다음 구조화 값을 받으며 `schema_version=0.7.0`만 허용한다.

- `request_mode`: `generate`만 허용하며 v0.4와 `improve` 입력은 schema validation에서 거부
- `required_places`, `preferred_places`, `excluded_places`
- `discovery`: 추가 장소 허용 여부, 분류, 최대 개수
- `party`: 성인·어린이·고령자·이동보조 여부
- `transport`: 허용·선호·fallback 수단, 선택 정책, 환승 한도
- `walking`: 직접 도보 허용, access/단일구간/일일 한도, 계단 회피 필수 여부
- `rest`: 강도, 최소 휴식, 연속 활동과 호환용 편의조건
- `food`, `total_budget_krw`(버스·택시 이동비 한도)
- `original_text`: 선택 보조 원문
- `previous_days`: 날짜·활동창·실제 시작/종료·역할별 canonical 장소·totals·폐쇄된
  evidence fact ID만 포함한 최대 4개의 최소 선택 이력. 이전 전체 타임라인·설명·사용자
  원문은 받지 않는다.
- `multi_day`: 관광지 중복 금지, 식사·휴식 soft avoid, 숙박 간격, 여행 전체 이동비 정책

택시비는 공식 요율과 경로 fact에서 범위로 제공하지만 택시 전용 상한으로 후보를
탈락시키지 않는다. 사용자가 `total_budget_krw`를 명시한 경우에만 전체 버스·택시
이동비 합계 한도를 적용한다.

`selection_policy=cost_time_balance`는 직접 도보 15분, 버스 대기 30분, 버스 추가시간
30분, 버스 최소 절약액 5,000원, 택시 호출 계획 버퍼 10분을 기본 입력으로 노출한다.
검증된 버스와 택시의 문-to-문 시간 및 요금 범위 중간값을 비교하며, 시각이 없는 route
pattern은 `BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED` 대안으로 보존한다. `ModeDecision`은
버스 추가시간·절약액이 각각 입력 문턱에서 얼마나 남거나 초과했는지를 margin으로 제공한다.
시간 문턱 5분 이내 또는 절약액 문턱 1,000원 이내이면
`selection_near_threshold=true`와 `BUS_SELECTION_NEAR_THRESHOLD`를 함께 표시한다.

구조화 입력과 원문 해석이 충돌하면 구조화 입력이 우선한다. 원문에서 보충한 값은 `assumptions`에 남긴다.
`auto_schedule_meals` 또는 `auto_schedule_cafe`가 켜진 성공 응답은 이름 있는 실제 장소를
포함한다. 운영시간 fact가 없으면 `operating_hours_status=UNVERIFIED`, `opens_at=null`,
`closes_at=null`, `OPENING_HOURS_UNKNOWN` 경고와 `feasible_with_caution`을 사용한다. 이 상태는
영업 중이라는 주장이 아니며 활동 근거는 `UNVERIFIABLE`, 종합 근거는 `partial`로 유지된다.
시간 산술에 문제가 없으면 `timing_status=on_schedule`이다. 장소 미지정 buffer는 식사나
휴식으로 세지 않는다.
입장료·식사비·음료비는 수집하거나 합산하지 않으며, 응답 비용은 버스·택시 이동비만 포함한다.
인원 구성은 공식 버스요금 범위를 계산하는 입력일 뿐 별도 readiness 조건이 아니다. 성인 한 명
구성으로 제한하지 않으며, 주민 할인이나 카드 보유 여부는 계속 가정하지 않는다.

## 정확 일정 판정 입력과 응답

`EvaluateJejuDayTripInput`은 `schedule_format` discriminator를 사용한다.

- `activities_only`: 활동 시각만 받고 기본적으로 숙소→첫 활동→…→숙소 이동을 엔진이
  삽입한다. 실시간 남은 일정처럼 출발지가 숙소와 다르면 선택적 `start_location`을 첫 이동
  출발점으로만 사용하고, 마지막 이동은 계속 `day_boundary.end_place`로 향한다.
- `full_timeline`: 사용자가 입력한 이동까지 검증한다. 버스번호·시간·거리·정류장은
  공식 근거와 일치해야 사실로 승격된다.

두 형식의 필드를 섞으면 계약 오류다. 종합 상태와 별도로
`timing_status=on_schedule|at_risk|disrupted|unknown`,
`evidence_status=verified|partial|unavailable`을 제공한다. 운영시간만 모르면 시간 상태를
정상으로 유지하고 근거 상태만 partial로 둔다. 수정안은 수정된 전체 타임라인의 재검증을
통과한 경우에만 원본과 별도로 반환한다.

판정과 재판정은 여행일의 장소·서비스영역 및 허용 이동수단 capability가 준비된 경우에만
runtime evidence adapter를 호출한다. 버스는 미래 시간표·confirmed 정류장 mapping·버스 요금,
택시는 주행 경로·택시 요금, 도보는 도보 경로 readiness를 각각 요구한다. 허용 수단 중
하나라도 닫히면 planner가 그 수단을 시도하지 않도록 수치를 만들기 전에
`unverifiable`/`data_unavailable`로 종료한다.

## 실시간 재판정

`RevalidateJejuDayTripInput`은 원 일정, `checked_at`, 진행 상태, 선택적 GPS를 받는다.
`state=at_place`에는 `current_event_started_at`이 필수이며 남은 체류시간은 이 실제
시작시각으로 계산한다. 탑승 중 위치는 추정하지 않고 `ON_BUS_PROGRESS_UNSUPPORTED`를 남긴다.
GPS가 없는 장소 체류 상태는 검증된 이벤트 입구를 기준으로 제한 판정한다. 이동 중인데
GPS·정류장·노선 정보가 모두 없으면 위치 기반 재경로를 만들지 않고
`data_unavailable`과 `LOCATION_CONTEXT_INSUFFICIENT`를 반환한다.
남은 일정은 현재 장소 또는 GPS를 `start_location`으로 변환하되 원 일정의 숙소를
`day_boundary.end_place`를 보존한다. 현재 장소와 첫 남은 활동이 같으면 외부 경로 수치를 만들지
않고 0분 위치 연속성으로 처리한다. GPS endpoint는 요청 프로세스 메모리에만 존재한다.

## 생성 최상위 응답

`DayTripResponse` 필드는 다음과 같다.

- `schema_version`: 항상 `0.7.0`
- `request_id`, `generated_at`
- `status`: `success | insufficient_feasible_routes`
- `planning_context`, `request`, `assumptions`
- `recommendations`, `place_decisions`, `evidence_facts`, `data_sources`
- `capability_coverage`
- `global_warnings`, `validation`, `failure`

부분 성공 상태는 없다. 성공은 `balanced`, `relaxed`, `experience_max`가 각각 한 개씩인 정확히 세 추천을 요구한다. 세 추천의 장소 순서 또는 이동 방식이 실질적으로 달라야 한다. 한 전략이라도 유효 후보가 없으면 추천 배열을 비우고 `insufficient_feasible_routes`를 반환한다.

TMAP 파생 경로 근거가 포함된 생성 후보는 프로세스 메모리에서 최대 23시간 50분만
유효하다. `plan_expires_at`은 이 한도를 넘지 않는다. 프로세스 재시작이나 만료로 후보
본문이 사라지면 기존 후보를 복구했다고 주장하지 않고 `CANDIDATE_EVIDENCE_UNAVAILABLE`로
재생성을 요구한다. 승인된 durable projection이 생기기 전에는 후보 적용 기능을 기본 OFF로 둔다.

이전 날짜의 `visit`은 다시 추천하지 않는다. `meal`과 `rest`는 새 후보가 없을 때만
`REPEAT_MEAL_FALLBACK` 또는 `REPEAT_REST_FALLBACK`과 함께 재사용한다. 각 추천의
`transport_selection_summary`는 선택 수단 수, feasible/unverifiable 버스 대안 수,
임계값 인접 구간과 버스 탈락 사유를 집계한다.

## evidence lineage

외부 사실과 계산값은 다음 순서로 추적한다.

```text
data source → source fact → evidence fact → computed fact → timeline → recommendation reason
```

`EvidenceFact`에는 `fact_id`, `category`, `value`, `unit`, `source_refs`, `data_as_of`, `retrieved_at`, `confidence`, `is_estimated`, `derivation`이 있다. source fact는 `source_refs`가 필수다. computed fact는 공식/외부 fact와 정책 fact ID를 `input_fact_ids`로 참조한다. 응답 내 미지 fact ID 참조는 전체 응답 검증에서 거부한다. `DayTripResponse`, `EvaluationResponse`, `PreviewTransferResponse`는 source fact가 참조한 모든 `source_id`를 같은 응답의 `data_sources`에 포함해야 하고, 중복되거나 미지인 source ID를 거부한다. 이동 미리보기 출력도 `evidence_facts`와 함께 `data_sources`를 제공한다. 장소 검색과 정류장 조회의 `source_refs`는 임시 active-view 이름이 아니라 `config/data_sources.toml`의 canonical source ID를 사용한다.

버스·택시 운임 coverage는 각각 `bus_fare_policy_ready`,
`taxi_fare_policy_ready`로 발행한다. 이전 `fare_policy_ready` publication은 source ID에 따라
두 capability 중 하나로 안전하게 해석하지만 새 발행은 분리된 이름을 사용한다.

## 버스와 도보

`mode=bus`인 모든 transfer는 다음을 포함한다.

1. `access_walk`: 이전 장소 endpoint에서 정확한 승차 정류장까지
2. 하나 이상의 `bus_rides`: canonical/provider 정류장 ID, 좌표, 방향, 노선, 예정시각
3. 필요한 `transfer_walks`
4. `egress_walk`: 정확한 하차 정류장에서 다음 장소 endpoint까지

성공 bus ride의 `mapping_status`는 `CONFIRMED`만 가능하다. walk endpoint의
`entrance_verification`은 검증 입구의 `VERIFIED` 또는 장소 대표좌표의
`PROVISIONAL_PLACE_POINT`다. 대표좌표를 사용해도 정류장 ID·방향·서비스데이·stop-time은
완화하지 않는다. 이동보조·계단 회피 필수 요청에는 대표좌표 fallback을 사용하지 않는다.

도보 시간은 다음을 분리한다.

```text
planned_minutes = ceil(expected_minutes * speed_multiplier) + route_uncertainty_minutes
```

승차 권장 도착은 예정 출발보다 최소 `boarding_buffer_minutes`만큼 빨라야 한다. 택시 대체안은 확정요금이 아닌 `fare_min_krw`·`fare_max_krw` 범위와 `is_estimated=true`를 반환한다.
실시간 배차 예상값이 없을 때의 택시 호출 대기는 versioned 계획 정책의 안전 버퍼로
별도 표시하며 실제 평균이나 실시간 값으로 표현하지 않는다.

## 시간과 합계

- 추천의 `day_start_at`·`day_end_at`은 각각 첫 timeline 이벤트 시작과 마지막 이벤트
  종료에 정확히 일치하고, `start_place_id`·`end_place_id`는 확정된 하루 경계 ID다.
  기존 `accommodation_departure_at`·`accommodation_return_at`도 v0.7 전환 기간 동안 같은
  시각을 보존하지만 장소 경계 의미는 새 필드가 기준이다.
- 방문의 `arrival_at`·`entry_at`·`departure_at`·`stay_minutes`는 timeline 방문
  이벤트와 일치하며, 성공 추천의 `opening_hours_conflict`는 항상 `false`다.
- 구간별 `segment_risks`는 `risk`와 `slack_minutes`를 제공한다. 고정된 다음
  시각이 없어 여유를 정의할 수 없으면 숫자를 만들지 않고 `null`을 반환한다.
- `duration_minutes == end_at - start_at`
- 이벤트 sequence는 유일하고 오름차순이다.
- 이벤트는 겹치지 않는다.
- `totals.total_minutes`와 유형별 합계는 timeline 집계와 일치한다.
- `totals.estimated_cost`는 `bus_cost + taxi_cost`와 일치한다.
- 확인되지 않은 운영시간·시간표·출입구를 AI 추정으로 채우지 않는다.
- 택시 호출 계획 버퍼는 주행 transfer 앞의 별도 `buffer` 이벤트이며
  `is_live_dispatch_estimate=false`다.
- `schedule_window_fit`은 운영시간 검증과 분리된 순수 시간창 산술 결과다.

## 보관된 v0.5 자료

`docs/examples/v0.5`와 `docs/examples/v0.6`은 당시 계약 기록으로만 보관한다. 현재 Pydantic
모델로 변환하거나 검증하지 않으며 current synthetic 예시는 `docs/examples/v0.7`에서 생성한다.

## 단일 first-pass

동부 relaxed 검증용 단일 결과는 공개 추천 성공이 아니며 항상
`production_recommendation=false`다. Generate→Evaluate→synthetic Revalidate는 같은
타임라인에서 대기·이동·활동과 선택하지 않은 대안을 보존한다. 이 경로는 세 전략 공개
성공 계약을 완화하지 않으며, 정식 `recommend_jeju_day_trips` 응답은 계속 세 추천 또는
`insufficient_feasible_routes`만 반환한다.

## 실패

실패 응답은 `failure.code`, `message`, `reason_codes`, `missing_capabilities`를 제공한다. 데이터 부족은 MCP protocol 내부 오류가 아니다. 주요 reason code는 [REASON_CODES.md](./REASON_CODES.md)에 정의한다.
