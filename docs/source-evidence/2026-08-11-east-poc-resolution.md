# 제주 동부 PoC 차단조건 순차 해소 기록

확인일: 2026-08-11

## 1. 2026-08-15 서비스데이

- 공식 XLSX checksum:
  - 201: `d3a6b9db15f0786a1ac1ab7865a7762f51b7521ef69e9844b1ca6e19b0e2b04e`
  - 211·212: `2e0e13c4d980b7edaaa7540e2ffc013bdb3d43e9e19c7a46caccc66584c615eb`
- 6개 worksheet 전체에서 `평일`, `주말`, `토요일`, `공휴일`, `휴일`, `매일` 표기를
  검사했으나 서비스데이 근거를 찾지 못했다.
- 제주 BIS 공지와 시간표 화면에서도 201·211·212의 광복절 공통/전용 운행 근거를 찾지
  못했다.
- 시간표 화면의 실제 선택 구조를 확인한 결과 201은 `GSCHEDULE_ID=405009`, 211·212는
  `GSCHEDULE_ID=405011`의 단일 XLSX 다운로드로 연결된다. 화면에는 요일 선택이나 공휴일
  적용 표기가 없고, 다운로드 전 조회도 해당 schedule의 존재 여부만 검사한다. 단일 파일이라는
  사실은 공휴일 공통 운행의 명시적 근거로 사용하지 않는다.
- 판정: `BUS_SERVICE_DAY_UNVERIFIED`. HOLIDAY calendar를 생성하지 않는다.

## 2. 운영시간·입구

- `tourapi.place-intro` 7개 장소 aggregate query는 contract preflight를 통과했지만 실제
  수집은 `PAGINATION_INTERRUPTED`로 incomplete 처리됐다.
- 기존 제주 전체 partial raw는 1,019건 중 939건 완료(92.15%)이고, 운영시간 문자열 847건
  중 21개 장소만 구조화 가능했다. incomplete snapshot은 active로 승격하지 않는다.
- 동부 manifest 후보 13곳만 다시 대조한 결과 12곳은 부분 snapshot에 있었지만 검증 가능한
  요일별 시간 구간은 0곳이었다. 제주레일바이크 1곳은 부분 snapshot에 없었다. 원문을 로그나
  공개 문서로 복사하지 않고 정규화 결과와 거부 코드만 확인했으며 `opening_hours_ready`는
  활성화하지 않았다.
- TourAPI 중심좌표 7건은 모두 입구가 아니므로 `UNVERIFIED`를 유지한다.
- 비짓제주 현재 상세 페이지에서 2026-08-15에 적용 가능한 두 운영시간을 별도 수동 fact로
  정규화했다.
  - 성산일출봉: 하절기 04:30~20:00, 입장마감 19:00
  - 제주레일바이크: 09:00~17:00, 연중무휴
- 운영시간 staged publication: `957a66ef-4eb5-4f5b-9e15-a84de96e85e3`
- 숙소를 활동 운영시간 분모에서 제외한 측정 결과: 2/7, coverage `0.285714`
- coverage 1.0이 아니므로 publication은 `STAGED`로 유지하고 active pointer를 변경하지 않았다.
- 추가 웹 검색에서 비짓제주 영문 상세는 섭지코지를 `Open year-round`로 안내하지만 시작·종료
  시각은 제공하지 않았다. 광치기해변과 호랑호랑 현재 상세도 운영 시작·종료 구간이 없었다.
  성산 부뚜막식당의 09:00~20:00 정보는 제3자 출처에서만 확인되어 운영 fact로 채택하지 않았다.
- 후속 확인에 필요한 기관·사업자 요청 문안과 현장 입구 검증 요건은
  `docs/operations/2026-08-11-east-poc-external-verification.md`에 분리했다. 장소 가격은
  제품 범위에서 제외했으며 회신 전에는 대표좌표를 입구로 승격하지 않는다.

## 3. TAGO route pattern과 공식 XLSX 행 매핑

- source: `tago.bus-route-stops`
- staged publication: `c516eb03-186c-4de4-9788-6c1b4d13b2e9`
- dataset: `2026-08-11-c754c0f40473`
- provider pattern: 38개(201 20개, 211 6개, 212 12개)
- route-stop 행: 4,239개
- 공식 좌표 보유: 4,239개(100%)
- 정규화 거부: 0개
- 중복 `(route, sequence)`: 0개

공식 XLSX의 잘못된 `A1` worksheet dimension은 원본을 수정하지 않고 read-only reader의
dimension을 재탐색하도록 보정했다. 201의 오조·세화고·효돈초교 선택 경유, 211·212의
금백조로·송당초·수산2리·수산초 선택 경유, 출발 방향·부분 운행 endpoint, 212의 사려니숲길
경로를 TAGO 순서와 비교했다.

- 실제 운행행: 180개
- unique pattern match: 180개
- ambiguous: 0개
- unmatched: 0개

판정: route-pattern 유일 매핑 조건은 통과했다. 다만 서비스데이가 닫혀 있으므로 timetable
publication과 `future_bus_planning_ready`는 활성화하지 않는다.

### stop identity staging

- route pattern에 등장한 unique provider stop: 611개
- active TAGO stop fact 좌표 일치: 611개(1m 이내)
- provider ID·이름 보유: 611개
- canonical namespace: `jeju.stop:tago:{provider_stop_id}`
- mapping method/status: `OFFICIAL_ID` / `CONFIRMED`
- staged publication: `f33e2d0c-9df7-4a57-a4da-2f23bdc9ab8c`

provider ID와 canonical ID는 별도 필드로 보존했다. exact scope manifest의 필수 STOP 분모가
없던 상태에서 대표 장소 좌표를 입구로 사용하지 않고 정류장 후보 축소에만 사용했다. 각 scope
장소에서 201·211·212 경유 정류장 중 가까운 후보를 최대 2개로 제한한 결과 unique STOP
18개를 exact scope에 추가했다.

- 갱신 scope publication: `efa51d7d-151e-4e3d-aaaf-7d3003508516`
- 필수 STOP: 18개
- `OFFICIAL_ID` / `CONFIRMED` mapping: 18개
- `confirmed_stop_mapping_ready`: coverage 1.0, active

coverage 측정 importer가 active scope 분모를 읽지 못하던 권한 결함은
`0007_scope_coverage_permissions.sql`로 최소 SELECT 권한만 추가해 수정했다.

## 4. live 조건

- TAGO route-stop API: raw-first 수집과 staging 검증 통과
- TMAP driving adapter: memory-only 1차 프리뷰 3개 일정 조립 통과
  - 실제 이름이 있는 식사 장소와 휴식 장소를 세 일정 모두에 포함
  - 09:00~19:00 숙소 왕복 경계와 점심 시간창을 정책 fact로 계산
  - TMAP route fact와 제주 택시 운임 정책만으로 이동 거리·택시비 범위를 계산
  - 장소·식사·음료 가격은 수집하거나 합산하지 않음
  - 실제 TMAP 수치와 응답은 파일·DB·MinIO·로그에 저장하지 않음
  - 입구와 일부 운영시간이 미검증이므로 `production_recommendation=false` 유지
- TAGO arrival: scope mapping은 준비됐지만 미래 일정·입구 readiness가 닫혀 있어 호출하지 않음
- 2026-08-15 당일 재판정: 미래 날짜이므로 아직 실행할 수 없음

## 5. scope manifest와 교통 운임

- 동부 scope publication: `efa51d7d-151e-4e3d-aaaf-7d3003508516`
- 활성 구성: 장소 13개, 필수 STOP 18개, 전략 template 3개, candidate 연결 12개
- `scope_manifest_ready`: `JEJU_EAST/POC_V1`, 2026-08-11~2026-08-15, coverage 1.0
- 버스 운임 publication: `7b7afe89-026d-4bc9-a4ea-e6d3891dc831`
- 택시 운임 publication: `927c03bd-3217-4b44-b380-2135f82b7702`
- 두 정책 모두 2026-08-15 유효성 측정을 통과했고, active coverage의
  `fare_policy_ready`는 두 source record에 대해 `bool_and=true`다.

장소·식사·음료 가격은 수집·합산하지 않았다. 비용 결과의 근거는 위 버스·택시 정책으로만
한정한다.

## 현재 판정

`Partial`

해결됨:

- TAGO 38개 route pattern 수집
- route-stop 공식 좌표 보존
- 공식 XLSX 180개 운행행의 provider pattern 유일 매핑
- 동부 scope manifest 발행 및 활성화
- 2026-08-15 버스·택시 운임 정책 발행 및 활성화
- 필수 STOP 기반 confirmed mapping coverage 측정 구현
- 동부 정류장 후보 18개 mapping coverage 1.0 활성화
- 성산일출봉·제주레일바이크 운영시간 fact staging
- 이름 있는 식사·휴식 장소와 택시 route fact를 포함한 3개 memory-only 1차 프리뷰

잔여 외부 차단:

- `BUS_SERVICE_DAY_UNVERIFIED`
- `VERIFIED_ENTRANCE_MISSING`
- `OPENING_HOURS_COVERAGE_INCOMPLETE` (2/7)
- `SAME_DAY_REALTIME_SMOKE_PENDING`
