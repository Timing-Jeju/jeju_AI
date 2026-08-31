# 제주 동부 순차 목표 진행 상태

최초 기준일: 2026-08-11  
최근 실행일: 2026-08-14  
대상 여행일: 2026-08-14 금요일, 2026-08-15 토요일(광복절)  
전체 상태: `Partial`

## Goal 1 — relaxed first-pass

상태: 구현·offline 검증 완료

- 고정 relaxed 동선에 Generate → Evaluate → synthetic Revalidate를 연결했다.
- `cost_time_balance`가 도보·버스·택시를 같은 출발시각에서 비교한다.
- 택시 호출 계획 버퍼와 실제 주행을 별도 타임라인 이벤트로 보존한다.
- first-pass는 `production_recommendation=false`이며 공개 세 추천 계약을 변경하지 않는다.

## Goal 2 — 평일 시간표 publication

상태: 완료

- 공식 XLSX와 명시적 mapping/service-day manifest를 raw-first 방식으로 검증·발행하는
  `official-bus-timetable` importer를 구현했다.
- staged route-stop 4,239행과 공식 XLSX 운행 180행으로 owner-only raw ZIP을 재생성했다.
- publication `2d49ecf2-8fe0-4ac4-a3a6-4ce70e3645b6`을 활성화했다.
- active projection은 route-stop 4,239행, 평일 calendar 1행, trip 180행,
  stop-time 1,321행이며 2026-08-14 coverage는 1.0이다.
- 정류장 identity와 버스·택시 요금의 기존 active publication에도 immutable coverage 행을
  append해 2026-08-14 coverage 1.0을 연결했다.

2026-08-15 공휴일에는 이 평일 publication을 재사용하지 않는다.

## Goal 3 — 운영시간 coverage와 입구 품질 개선

상태: 운영시간은 비차단 주의 상태로 전환, 공식 정규화 데이터는 여전히 0건

- relaxed 장소의 대표좌표를 검증 입구로 승격하지 않고 명시적인 provisional endpoint로 사용한다.
- 운영시간이 없는 장소에 임의 영업시간을 만들지 않았다.
- 승인된 TourAPI place-intro 7개 질의를 raw-first로 재수집했으나 모두 HTTP 429였으므로
  acquisition을 `INCOMPLETE`로 닫고 raw·publication을 만들지 않았다.
- TourAPI의 `totalCount=0`, `numOfRows=0` 상세 응답을 정상 빈 결과로 처리하도록
  페이지네이션을 수정했고, 외부 오류는 안전한 result code만 보존한다.
- 2026-08-14 동부 후보 13개 aggregate 수집은 완료됐지만 정규화 가능한 검증 운영시간이
  0건이었다. 빈 publication을 만들지 않고 `QUALITY_FAILED/NORMALIZED_RECORDS_EMPTY`로 닫았다.
- 일반 요청은 운영시간을 만들지 않은 `UNVERIFIED` 활동과
  `OPENING_HOURS_UNKNOWN` 경고로 계속 생성한다. 공식 휴무가 확인되면 계속 제외한다.
- coverage 계산은 요청한 단일 여행일·요일에 유효한 `VERIFIED` 운영 규칙과, 여행일까지
  유효하며 만료되지 않은 `VERIFIED` 입구만 인정하도록 보강했다.
- 수집 양식과 raw-first 발행 순서는 기존 외부 사실 확인 요청서와 predata checklist에 있다.

완료 조건: 방문 장소 전체의 날짜별 운영시간 coverage 1.0을 측정한 뒤 활성화하면
`UNVERIFIED` 주의를 제거할 수 있다.
입구 publication은 일반 추천의 완료 조건에서 제외하고 이동보조 요청과 위치 정밀도 개선에 사용한다.

## Goal 4 — 세 전략 공통 엔진 적용

상태: 런타임 코드·offline 검증·8월 14일 live 세 추천 완료

- production routing 경로가 `cost_time_balance`를 사용하도록 연결했다.
- 선택하지 않은 대안과 비교 evidence를 보존하고, TMAP cache hit는 외부 호출 예산에서
  제외한다.
- production generation에서도 택시 호출 버퍼와 주행 이벤트를 분리한다.
- 검증 입구가 없는 `preview_jeju_transfer`도 active 장소 대표좌표를 provisional endpoint로
  사용하도록 generation과 동일하게 연결했다.
- 비용·시간 비교 전에 같은 route pattern·방향·정류장 순서와 exact stop-time이 확인되는
  가장 가까운 정류장 한 쌍을 선택한다. 이로써 숙소→성산 구간의 공식 버스 후보가
  `feasible` 대안으로 보존되며, 선택 결과에는 택시 대비 시간차 reason code가 남는다.
- 0003에서 추가된 입구 검증시각·만료시각·지원수단 열이 runtime view에 빠져 있던 결함을
  `0011_rebuild_active_place_entrance_view.sql`로 수정하고 로컬 운영 DB에 적용했다.
- 승인된 TMAP driving contract로 세 전략의 메모리 전용 staging preview를 실행했다. raw body와
  geometry는 저장하지 않았고 `production_recommendation=false`를 유지했다.
- 공개 응답은 서로 다른 유효 추천 3개가 아니면 계속
  `insufficient_feasible_routes`를 반환한다.
- TMAP timeout은 예외를 노출하지 않고 해당 수단을 근거 없음으로 닫는다. 출발시각을
  전송하지 않는 동일 좌표 경로는 전략 간 메모리 cache를 재사용한다.
- 최대 계획속도에서도 직접 도보 15분 문턱을 넘는 것이 확실하면 TMAP 도보 호출을 생략하고
  `WALK_EXCEEDS_DIRECT_LIMIT_LOWER_BOUND` 미선택 대안을 보존한다.
- 같은 출발시각의 도보·버스·택시와 버스 양끝 도보는 실행예산 동시성 안에서 계산한다.
- 세 전략을 같은 전역 외부호출 동시성 제한 안에서 병렬 조립하고, 같은 TMAP cache key는
  single-flight로 한 번만 조회한다.
- 직통 정류장 후보 SQL은 출발·도착 각각 최근접 12개로 먼저 제한해 전체 조합 탐색 병목을
  제거했다.
- 8월 14일 live 실행은 13.1초에 서로 다른 추천 3개를 반환했다. relaxed 선택 수단은
  택시 4구간·도보 1구간이고 버스는 비교 결과 선택되지 않았다.
- relaxed 첫 두 택시 구간의 버스 대안은 `feasible`이지만 택시 대비 추가시간이 기본 30분을
  넘어 미선택됐다. 다음 두 택시 구간에는 exact stop-time 버스가 없고 마지막은 15분 이내
  도보다.
- 함덕·월정리해안도로를 기존 구성원 손실 없이 추가한 `east-poc-v2` scope bundle을
  raw-first로 발행했다. active publication은 `3443b6f5-099b-4728-bd6b-f973094e7df2`,
  구성원은 31→33건이며 2026-08-14 `scope_manifest_ready=1.0`이다.
- 추가 필수 장소는 기존 템플릿에 누적하지 않고 핵심 방문·점심 뒤의 선택 방문을 대체한다.
  장거리 세 전략은 활성 식당·휴식 대체 후보 조합으로 다양성을 유지한다.
- 함덕 필수 8월 14일 공개 생성은 정확히 세 추천을 반환했고 모두 19시 전에 복귀했다.
  한 실행의 balanced는 택시 3·버스 1·도보 1이었고, relaxed와 experience_max는 각각
  택시 4·도보 1이었다. 후속 재실행에서는 balanced도 버스 0구간이어서 TMAP 택시 비교값에
  따라 수단 경계가 달라질 수 있음을 확인했다. 원문·geometry·세부 수치는 저장하지 않았다.

완료 조건: runtime DB/TMAP과 평일 교통 facts를 갖춘 상태에서 relaxed, balanced,
experience_max 세 결과가 모두 유효함을 live E2E로 확인한다. 운영시간 미확인은 별도
`UNVERIFIED` 주의로 남긴다.

## Goal 5 — 8월 15일 service day

상태: 공식 근거 미확인

- 제주 BIS의 2026년 일반 시간표와 노선 변경 공지는 확인했지만, 201·211·212에
  2026-08-15 광복절 시간표를 적용할 수 있다는 공식 근거는 확인하지 못했다.
- 2026-07-23까지 표시되는 BIS 공지 목록을 재검토했다. 2019년 8월 15일 변경 공지는
  연도·시행 시간표가 다르므로 2026년 service-day 근거에서 제외했다.
- 평일 XLSX나 다른 노선의 공휴일 문구를 해당 세 노선의 `HOLIDAY` calendar로 재사용하지
  않는다.

완료 조건: `201`, `211`, `212`, `2026-08-15`, 적용 시간표 또는 예외가 명시된 제주 BIS·
제주특별자치도 공식 HTTPS 게시물이나 식별 가능한 담당 기관 서면 회신을 raw-first로
등록·발행한다.

## Goal 6 — 여행 당일 재판정

상태: 당일 생성·Evaluate·정적 Revalidate 완료, 선택 버스 TAGO는 미실행

- 2026-08-14 14:31 KST에 고성환승정류장 성산 방향 TAGO 도착정보를 memory-only로 조회해
  정규화에 성공했고 201 계열 도착정보가 포함됨을 확인했다. 원문은 저장하지 않았다.
- 같은 날 숙소→성산 구간의 TMAP·공식 평일 시간표 비교도 memory-only로 성공했다.
- 운영시간은 비차단 주의 상태로 전환됐다. `cost_time_balance` 14일 live 생성은 승인 근거와
  40회 호출 상한을 유지하면서 13.1초에 세 추천을 생성했다.
- 동일 relaxed 타임라인 Evaluate는 3.8초에 `unverifiable`,
  `schedule_window_fit=true`로 끝났다. 사유는 `OPENING_HOURS_UNKNOWN`과 휴식 권고다.
- 원본·남은 일정이 같은 운영시간과 경로 evidence를 요청 내부에서 재사용하도록 캐시했고,
  full-timeline의 활동 사이 선택 수단을 남은 일정 제약으로 보존했다.
- `activities_only.start_location`을 추가해 현재 위치는 첫 이동 출발점에만 쓰고 최종 구간은
  원래 숙소로 복귀하도록 계약과 평가 엔진을 수정했다. GPS endpoint는 요청 메모리에만 둔다.
- 실제 Revalidate는 6.0초에 남은 평가를 생성했다. 첫 출발지는 성산일출봉, 최종 도착지는
  플레이스 캠프 제주로 확인됐다. 운영시간 미확인 때문에 남은 평가는 `unverifiable`, 최상위는
  `data_unavailable`이며 timeout은 아니다.
- 선택 버스가 없어 TAGO는 호출하지 않았다. 원문·geometry·세부 TMAP 수치는 저장하지 않았다.
- 공식 stop-time의 PostgreSQL UTC 반환값을 `Asia/Seoul`로 정규화하고 `BusRide` Pydantic
  계약에서 세 시간 필드 모두 `+09:00`을 강제했다.
- 16시대 함덕→성산 preview의 다음 공식 201번은 20:40 출발·21:33 도착이라 19시 복귀
  후보가 아니다. 같은 함덕 정류장 TAGO snapshot은 메모리에서 16건을 정규화했지만 계획
  노선은 `REALTIME_NO_DATA`였다.
- TAGO snapshot 조회시각이 재판정 시각보다 5초 넘게 미래면 stale로 거부해, 현재 데이터를
  과거 계획에 적용하지 못하도록 보강했다.
- 승인된 TourAPI 레일바이크 상세소개를 raw-first로 1회 재조회했지만 원본 0건,
  `INCOMPLETE`, 운영시간 `unparseable`로 닫고 활성화하지 않았다.
- 정상·20분 지연 synthetic 시나리오는 Goal 1에서 검증했다.

완료 조건: 버스가 선택된 일정에 TAGO adapter를 연결해 19시 숙소 복귀를 확인하고, 방문
장소 운영시간 coverage로 남은 평가의 `unverifiable`을 해소한다. 택시 10분 값은 계속 계획
버퍼로만 표현한다.

## 잔여 위험

| 위험 | 현재 처리 |
|---|---|
| 검증 입구 없음 | 일반 요청은 provisional 대표좌표 사용, 이동보조 요청만 차단 |
| 운영시간 없음 | 운영 판정 `unknown/unverifiable` 유지 |
| 일부 checkpoint의 provider ID 모호 | 해당 stop-time만 제외하고 보간하지 않음 |
| 광복절 근거 없음 | `BUS_SERVICE_DAY_UNVERIFIED` 유지 |
| 운영시간 없음 | 임의 시각 없이 `UNVERIFIED`·`OPENING_HOURS_UNKNOWN` 주의 처리 |
| 운영시간 미확인 | 남은 일정은 생성하되 `unverifiable/data_unavailable` 유지 |
| 함덕 선택 버스 실시간 | balanced의 버스 1구간은 생성됐지만 해당 구간 TAGO 재판정은 아직 미실행 |
| 함덕·월정리 scope | 장소 membership은 활성화했지만 월정리를 함께 넣은 세 추천 E2E는 아직 미검증 |
| 장거리 버스 편차 | 함덕 balanced에서 버스 1구간을 관찰했지만 후속 재실행은 0구간으로 선택 경계가 변동 |
| TourAPI 상세 0건 | `INCOMPLETE/unparseable`, 운영시간 publication 미생성 |

## 2026-08-14 품질 게이트

- 한글 테스트 설명, Ruff, Pyright: `Pass`
- offline pytest: 300 passed, 9 skipped(live 환경 분리)
- migration integration contract: 11 passed
- ephemeral PostGIS·MinIO live: 테스트 전용 환경변수가 없어 9 skipped
- 공개 생성 E2E: 13.1초, 서로 다른 추천 3개, relaxed 택시 4·도보 1·버스 0
- 동일 타임라인 Evaluate: 3.8초, `unverifiable`, `schedule_window_fit=true`
- 실제 Revalidate: 6.0초, 남은 평가 존재, 현재 장소 출발·원 숙소 복귀 확인
- Revalidate 판정: 운영시간 미확인으로 `data_unavailable`, 선택 버스가 없어 TAGO 미호출
- TourAPI 운영시간 재수집: 수집 완료 후 `QUALITY_FAILED/NORMALIZED_RECORDS_EMPTY`, active 변경 없음
- 함덕 필수 공개 생성 E2E: 정확히 3개, 전부 함덕 포함·19시 전 복귀,
  한 실행의 balanced 택시 3·버스 1·도보 1
- Pydantic Schema·synthetic example drift: 10 passed
- 함덕 balanced full-timeline Evaluate: 이벤트 17개, `schedule_window_fit=true`,
  운영시간 미확인으로 `unverifiable`; 해당 재실행에서는 선택 버스 0구간
