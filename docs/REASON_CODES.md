# Reason code 카탈로그

| 코드 | 의미 | 처리 |
|---|---|---|
| `SOURCE_UNKNOWN` | catalog에 source가 없음 | 네트워크 전 중단 |
| `SOURCE_PENDING` / `SOURCE_NOT_APPROVED` | 라이선스·계약 승인이 완료되지 않음 | capability 비활성 |
| `SOURCE_HOST_REJECTED` | host allowlist 밖 URL | 네트워크 전 중단 |
| `SOURCE_PATH_REJECTED` | path allowlist 밖 URL | 네트워크 전 중단 |
| `RAW_STORAGE_FORBIDDEN` | source가 원본 영속화를 금지 | object store 호출 전 중단 |
| `RAW_OBJECT_HEAD_MISMATCH` | 업로드 checksum 또는 길이 불일치 | acquisition 미등록 |
| `ACQUISITION_NOT_VALIDATED` | 검증되지 않은 acquisition 발행 시도 | 기존 snapshot 유지 |
| `MIGRATION_CHECKSUM_MISMATCH` | 적용된 migration 파일 변경 | migration 중단 |
| `COORDINATE_OUTSIDE_JEJU_PREFILTER` | 제주 빠른 좌표 사전검사 실패 | blocking quality issue |
| `OPENING_PERIOD_INVALID` | 같은 날 폐장이 개장보다 빠름 | row 거부 |
| `SOURCE_RESPONSE_EMPTY` | 완료된 상세소개 query가 장소 row를 반환하지 않음 | `NO_DATA` 관측 fact로 보존하고 운영시간은 추정하지 않음 |
| `ROUTE_SEQUENCE_DUPLICATED` | 노선 정류장 순번 중복 | publication 거부 |
| `ENTRANCE_UNVERIFIED` | 검증된 장소 입구 부재 | 이동 후보 거부 |
| `STOP_MAPPING_UNCONFIRMED` | 정류장 mapping 근거 부족 | 버스 후보 거부 |
| `TMAP_CACHE_TTL_OUT_OF_RANGE` | cache TTL이 23시간 50분 초과 | 설정 거부 |
| `TMAP_REQUEST_FAILED` | TMAP 호출 또는 정규화 실패 | raw body 없이 안전 로그 |
| `MODEL_UNKNOWN_EVIDENCE` | LLM이 미지 fact ID 사용 | 모델 출력 거부 |
| `OFFICIAL_TIMETABLE_PENDING` | 여행일 공식/계약 시간표 미확인 | 성공 추천 금지 |
| `PLACE_AMBIGUOUS` | 숙소·장소 후보를 하나로 확정할 수 없음 | 후보를 제시하고 계산 중단 |
| `PLACE_OUTSIDE_SERVICE_AREA` | 숙소·요청 장소 좌표가 active 제주 경계 밖임 | 경계 fact를 근거로 장소 해석 중단 |
| `OPENING_HOURS_UNKNOWN` | 활동일 운영시간 근거 부재 | 시간 상태는 유지하고 근거 상태를 partial로 표시 |
| `PLACE_CLOSED` | 활동이 검증된 개방 구간 밖에 있음 | 후보 제거 또는 원본 일정 불가능 |
| `LAST_ADMISSION_MISSED` | 활동 시작이 검증된 마지막 입장 이후임 | 후보 제거 또는 수정안 제시 |
| `ROUTE_EVIDENCE_MISSING` | 문-to-문 이동 근거 부재 | 해당 이동 판정 불명 |
| `NEGATIVE_TRANSFER_SLACK` | 공식 이동시간보다 일정 여유가 짧음 | 일정 불가능 |
| `USER_ROUTE_FACT_MISMATCH` | 사용자 이동 주장이 공식 경로와 불일치 | 원본 일정 불가능 |
| `USER_ROUTE_FACT_UNVERIFIABLE` | 공식 경로에 사용자가 주장한 수치가 없어 대조 불가 | 0 등으로 대체하지 않고 판정 불명 |
| `LOCKED_MODE_UNAVAILABLE` | 고정 이동수단의 검증 경로 부재 | 자동 변경 없이 일정 불가능 |
| `REQUEST_MODE_IMPROVE_REMOVED` | 구버전 improve 입력 | 판정 도구 사용 안내 |
| `LOCATION_CONTEXT_INSUFFICIENT` | 이동 중 위치·정류장·노선 정보 부재 | 위치 기반 재경로 금지 |
| `REALTIME_PROVIDER_UNAVAILABLE` | 버스 구간에 실시간 provider/coverage가 없음 | 정적 근거만으로 정상 판정 금지 |
| `REALTIME_NO_DATA` | 정류장 응답에 계획 노선 도착값이 없음 | 미운행으로 단정하지 않고 불명 처리 |
| `ACCESSIBILITY_UNVERIFIABLE` | 계단 회피 조건을 확인할 경로 정보가 없음 | 해당 보행·버스 연결 거부 |
| `ROUTING_BUDGET_EXHAUSTED` | 요청별 외부 경로 호출 예산 초과 | 미검증 후보 없이 전체 실패 |
| `PLANNING_TIMEOUT` | 생성 150초 timeout 도달 | 미검증 후보 없이 전체 실패 |
| `REQUIRED_CAPABILITY_UNAVAILABLE` | 요청 조건의 readiness flag가 꺼짐 | 추정 실행 없이 구조화 실패 |
| `REGION_SCOPE_NOT_READY` | 요청 장소가 active 제주 전역 경계 밖임 | 생성·평가·재판정 중단 |
| `DATE_SCOPE_NOT_READY` | 요청일이 active coverage 유효기간 밖임 | 날짜를 추정 전환하지 않고 중단 |
| `BUS_SERVICE_DAY_UNVERIFIED` | 공휴일에 적용할 공식 노선 서비스데이 근거가 없음 | 버스 후보 및 택시 우회 완성 금지 |
| `BUS_NO_DEPARTURE_WITHIN_30_MINUTES` | access walk와 7분 buffer 후 버스 대기가 30분 초과 | 검증된 경우에만 택시 검토 |
| `TAXI_RECOVERY_PRESERVES_NEXT_DEADLINE` | 택시 회복안이 다음 hard deadline을 지킴 | 전체 재평가 통과 시만 노출 |
| `WALK_WITHIN_15_MINUTES` | 직접 보행 planned time이 입력 문턱 이내임 | 도보 선택 |
| `BUS_FASTER_AND_CHEAPER` | 버스 문-to-문 시간이 택시 이하이고 중간값 비용이 더 낮음 | 버스 선택 |
| `BUS_SAVES_AT_LEAST_5000_WITHIN_30_MINUTES` | 버스 추가시간 한도 안에서 입력 최소액 이상 절약 | 버스 선택 |
| `TAXI_BUS_TIME_PENALTY_EXCEEDS_30_MINUTES` | 버스 추가시간이 입력 한도를 초과 | 택시 선택 |
| `TAXI_BUS_SAVINGS_BELOW_5000_KRW` | 버스 중간값 절약액이 입력 최소액 미만 | 택시 선택 |
| `BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED` | 직통 route pattern은 있으나 정확한 승하차 시각이 없음 | 버스를 검증 불가 대안으로 보존 |
| `BUS_WAIT_LIMIT_EXCEEDED` | 현재 구간의 최초 또는 두 번째 버스 대기가 입력 한도 초과 | 해당 버스 path 탈락 |
| `BUS_ACCESS_WALK_EXCEEDED` | 정책 배수·불확실성을 포함한 접근 또는 하차 도보가 20분 초과 | 해당 버스 path 탈락 |
| `BUS_SAVINGS_BELOW_THRESHOLD` | 버스의 비용 중간값 절약액이 입력 기준 미만 | 택시 선택 사유로 집계 |
| `BUS_EXTRA_TIME_EXCEEDED` | 버스 문-to-문 추가시간이 입력 기준 초과 | 택시 선택 사유로 집계 |
| `BUS_ROUTE_UNAVAILABLE` | 정확한 운행편은 있으나 접근·환승·하차 제약을 만족하지 못함 | 시간표 미확인과 분리해 탈락 집계 |
| `PLACE_ALREADY_VISITED_CONFLICT` | 현재 필수 관광지가 이전 선택 일정에 이미 포함됨 | 추천 생성 전 전체 입력 거부 |
| `REPEAT_MEAL_FALLBACK` | 새로운 식당으로 완주 가능한 후보가 없어 이전 식당을 재사용 | 추천·활동 issue에 사유 기록 |
| `REPEAT_REST_FALLBACK` | 새로운 휴식 장소로 완주 가능한 후보가 없어 이전 장소를 재사용 | 추천·활동 issue에 사유 기록 |
| `OVERNIGHT_REST_BELOW_PREFERENCE` | 직전 복귀부터 현재 출발까지 선호 숙박 간격 미달 | comfort 감점과 경고, 강제 실패 없음 |
| `ON_BUS_PROGRESS_UNSUPPORTED` | 승인 데이터로 탑승 중 현재 위치를 확정할 수 없음 | 위치 추정 없이 evidence partial |
| `ROUTE_SNAPSHOT_CHANGED` | TMAP 거리 스냅샷 차이가 10% 또는 500m 허용치 초과 | 거리만으로 실패시키지 않고 최신 duration으로 시간 판정 |
| `TAXI_PICKUP_BUFFER_MISSING` | 택시 주행 직전 정책 호출 버퍼가 없음 | full timeline 불가능 |
| `TAXI_PICKUP_BUFFER_MISMATCH` | 택시 호출 버퍼가 수단 결정의 정책값과 다름 | full timeline 불가능 |
| `TAXI_PICKUP_PLANNING_BUFFER_APPLIED` | 비실시간 택시 호출 계획 버퍼를 주행과 분리해 적용 | 별도 buffer 이벤트 생성 |
| `PLANNED_SAFETY_BUFFER` | 전략별 위험 여유 정책을 이동 후 실행 여유로 적용 | 수치를 추정하지 않고 정책 fact와 buffer 이벤트로 분리 |
| `OPERATING_OR_MEAL_WINDOW_WAIT` | 검증된 개장 또는 식사 시간창보다 먼저 도착 | 전략 안전 여유와 별개의 buffer 이벤트로 대기 |
| `ALREADY_AT_DESTINATION` | 실시간 재판정의 현재 장소와 남은 첫 활동 장소가 같음 | 외부 이동을 만들지 않고 0분 위치 연속성으로 처리 |
| `BUS_SELECTION_NEAR_THRESHOLD` | 버스 추가시간이 한도 5분 이내이거나 절약액이 기준 1,000원 이내임 | 선택 결과와 함께 시간·비용 margin을 표시하고 재검증 우선순위로 사용 |
| `MULTI_REQUIRED_ROUTE_MINIMUM_STAY_APPLIED` | v0.5 장거리 first-pass에서 필수 방문이 둘 이상일 때 정책 최소 체류시간을 적용함 | v0.6에서는 시간 초과 후 수선으로만 체류시간을 단축 |
| `COVERAGE_NOT_READY_FOR_ACTIVATION` | staged publication coverage가 1.0이 아니거나 blocking reason 존재 | 기존 active snapshot 유지 |
| `SNAPSHOT_COVERAGE_NOT_READY_FOR_ACTIVATION` | TourAPI 상세소개가 active 장소마다 정확히 하나의 관측 fact를 갖추지 못함 | 기존 active snapshot 유지 |
| `insufficient_feasible_routes` | 서로 다른 유효 추천 세 개 미만 | 추천 없는 전체 실패 |
