# 공식 데이터 출처 정책

확인 기준일: 2026-08-11

## 1. 출처 우선순위

같은 필드가 충돌할 때 다음 순서를 적용한다.

1. 해당 시설·운영기관이 여행 날짜에 대해 직접 제공한 공식 정보
2. 제주특별자치도·국토교통부·한국관광공사 등 공공 API
3. TMAP의 지정 시각 경로 결과
4. 내부에서 검증한 수동 보정 데이터
5. AI 추정값

AI 추정값은 외부 사실의 확정값이 될 수 없고 항상 `is_estimated: true`와 낮은 신뢰도를 가진다.

## 2. 분야별 출처

### 관광지와 운영시간

- 한국관광공사 국문 관광정보 서비스: https://www.data.go.kr/data/15101578/openapi.do
- Base URL: `https://apis.data.go.kr/B551011/KorService2`
- 핵심 기능: `searchKeyword2`, `detailCommon2`, `detailIntro2`, `detailInfo2`, `detailImage2`

`detailIntro2`는 콘텐츠 유형에 따라 필드가 다르므로 원문을 보존한 뒤 `open_at`, `close_at`, `last_admission_at`, `breaks`, `closed_dates`로 정규화한다. 누락 또는 해석 불가 값은 `unknown`이다.

- 한국관광공사 관광 분류 `A05020900`: https://data.visitkorea.or.kr/page/A05020900

카페는 이름으로 추측하지 않는다. TourAPI의 세 단계 분류 코드를 장소 fact에 보존하고,
공식 `A05020900`인 장소만 카페/전통찻집으로 분류한다. `contentTypeId=39`이지만 이 세부
코드가 아니면 음식점으로만 취급한다.

#### 비짓제주 보강 후보 검토

- 제주관광공사 비짓제주: https://www.visitjeju.net/
- 공식 OpenAPI 활용가이드: https://api.visitjeju.net/file/bbsattachment/filepath/202010/08/8e41d714-aa18-4d37-9e99-7aa5982dd913.pdf

2020년 V1.0 가이드는 제주관광공사가 제공하는 콘텐츠 ID·분류·주소·좌표·전화번호·태그를
서비스 키 기반으로 조회하는 API를 설명한다. 비짓제주 현재 웹 콘텐츠에는 성산일출봉의
계절 운영시간·정기휴무·주차장·접근성 안내처럼 TourAPI를 보강할 수 있는 정보가 있다.

하지만 가이드의 endpoint는 `http://`만 명시하고, 사용 제약 사항은 비어 있으며, 현재 이용조건·
보존 및 파생 허용·갱신주기·웹 상세정보와 API 응답의 동일성을 확인할 근거가 부족하다. 또한
가이드 응답 좌표는 콘텐츠 대표좌표로 설명될 뿐 출입구 좌표가 아니다. 따라서 현재 source
catalog과 자동 수집 profile에는 추가하지 않고, 제주관광공사의 서면 이용조건과 HTTPS endpoint,
실응답 coverage를 확인할 때까지 조사 후보로만 둔다.

### 버스 정류장·노선·도착정보

- TAGO 버스정류소정보: https://www.data.go.kr/data/15098534/openapi.do
- TAGO 버스노선정보: https://www.data.go.kr/data/15098529/openapi.do
- TAGO 버스도착정보: https://www.data.go.kr/data/15098530/openapi.do
- 제주 버스정류소현황: https://www.data.go.kr/data/15010850/fileData.do
- 제주 버스정보시스템: https://bus.jeju.go.kr/
- 제주 공식 시간표: https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule
- 시행일 포함 변경 공지: https://bus.jeju.go.kr/notice/list

정류장 ID와 좌표는 TAGO의 제주 도시코드 전체 정류소 목록을 우선하고, 운행 방향은 선택한
노선의 정류장 순서와 승차·하차 인덱스로 검증한다. 단일 좌표 500m 검색 결과를 제주 전역
snapshot으로 발행하지 않으며, 정류장 이름만으로 방향을 확정하지 않는다. 실시간 도착정보는
미래 계획에 사용하지 않고 출발 직전 재검증에만 사용한다.

`tago.bus-route-stops`는 `getRouteAcctoThrghSttnList` 계열 응답을 노선별 별도 snapshot으로
발행한다. 201·211·212의 provider route pattern과 모든 정류장 순서가 이 snapshot에 유일하게
연결돼야 하며, 기존 `tago.bus-route` active pointer를 대체하지 않는다. 정규화 v2는 TAGO의
`gpslati`·`gpslong`을 각 route-stop record에 보존해 좌표 coverage와 정류장 순서 기반 거리
계산을 검증한다.

공공데이터포털의 `제주특별자치도_제주버스시간표정보`는 제주 전 운행 버스 시간표를
제주 BIS에서 Excel로 내려받도록 안내하고, 이용허락범위를 제한 없음으로 표시한다.
따라서 `jeju.bus-timetable`은 공식 XLSX 묶음 ZIP의 내부 원본 보관과 직접 정규화 파생에
승인됐다. 가공 CSV는 승인 원천으로 사용하지 않는다. 다만 원본 파일의
공개 재배포는 별도 검토 전까지 금지하며, BIS 변경 공지의 시행일을 함께 반영해야 한다.

도착정보의 `arrtime`은 초 단위로 정규화하고 조회 후 60초까지만 사용한다. 응답 없음은 미운행이 아니라 `REALTIME_NO_DATA`이다.

### 도보와 차량 경로

- TMAP API: https://tmapapi.tmapmobility.com/
- TMAP API 약관: https://tmapapi.tmapmobility.com/terms.html

TMAP은 보행자·자동차·타임머신 자동차 경로를 제공한다. 보행 경로는 대표 중심 좌표가 아닌 출입구와 정류장 좌표 사이를 조회한다. TMAP 일반 API 데이터도 24시간 초과 저장 제한을 적용한다.

일반 자동차·보행 endpoint의 `data_as_of`는 요청한 미래 출발시각이 아니라 실제 조회시각이다.
미래 출발시각은 `requested_departure_at`, 일반 자동차 결과는
30분을 넘는 미래 요청에는 `traffic_basis=current_snapshot_proxy_for_future`와
`is_estimated=true`를 붙인다. 타임머신 adapter가 승인되기 전에는 현재 교통 결과를 미래 교통
사실로 표시하지 않는다.

### 공휴일과 서비스데이

- 한국천문연구원 특일 정보: https://www.data.go.kr/data/15012690/openapi.do
- Base URL: `https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService`

### 제주 택시 운임

- 제주 버스정보시스템 공식 택시 안내: https://bus.jeju.go.kr/publicTrafficInformation/taxiInfo
- 현재 저장 정책 기준 시행일: 2024-07-01

공식 요율과 TMAP 차량 거리·시간 fact로 최소·최대 범위만 계산한다. 배차, 호출료의 실제 부과, 미터기 최종금액은 보장하지 않는다.

여행 날짜의 공휴일 여부를 확인한 뒤 운행사의 평일·주말·공휴일 달력과 결합한다. 공휴일이라는 사실만으로 특정 버스 시간표를 선택하지 않고 노선 서비스 달력과 교차검증한다.

### 제주 서비스 경계

- SGIS 행정구역 경계: https://www.data.go.kr/data/15129688/fileData.do

2026-08-11 공식 페이지의 무료·이용허락범위 제한 없음 표시를 검토해 private 원본 보관과
내부 정규화를 승인했다. 공개 재배포는 허용하지 않는다. 원본 ZIP의 SHA-256은
`f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6`이며, DBF의
`BASE_DATE=20250630`을 데이터 기준일로 사용한다.

전국 ZIP은 private object storage에 그대로 보존하지만 projection에는 `SIDO_CD=39`,
`SIDO_NM=제주특별자치도`인 EPSG:5179 시도 경계만 단순화 없이 WGS84 MultiPolygon 한 건으로
발행한다. 장소·숙소·발견 후보·검증 입구의 최종 포함 판정은 active boundary의 `ST_Covers`를
사용한다. geometry는 공개 응답이나 로그에 포함하지 않고 경계 fact/publication 참조만 남긴다.

### 현재 범위에서 제외한 데이터

날씨, 한라산 통제, 도로 속도 교차검증, VisitJeju 보강 API, 공중화장실, TMAP 대중교통
경로는 v0.5 핵심 판정 엔진이 소비하지 않는다. 따라서 승인 source catalog, refresh profile,
수동 import 경로에 등록하지 않는다. VisitJeju는 위 보강 후보 검토의 승인 조건을 충족한 뒤,
나머지는 향후 제품 요구가 생기면 별도 계약·품질 게이트와 함께 다시 검토한다.

## 3. 필드별 최소 근거

| 반환 필드 | 최소 허용 근거 | AI 단독 생성 허용 |
|---|---|---|
| 관광지 좌표 | TourAPI 또는 공식/지도 POI | 금지 |
| 운영시간·휴무일 | 공식 시설/TourAPI | 금지 |
| 정류장 ID·좌표 | TAGO·제주·TMAP 정류장 데이터 | 금지 |
| 노선·운행 방향 | 노선 ID와 정류장 순서 | 금지 |
| 버스 계획시간 | 여행 날짜·시각 기반 공식/계약 경로 | 금지 |
| 도보 거리·예상시간 | 보행 경로 API | 원칙적으로 금지 |
| 계획용 도보시간 | API 예상시간 + 공개된 안전정책 | 허용 |
| 체류시간 | 내부 정책·AI 추천 | 허용, 추정 표시 |
| 추천 이유 | 검증된 일정 데이터 | 허용 |

## 4. JSON provenance 규칙

각 출처는 다음을 반환한다.

- `source_id`, `provider`, `dataset_or_api`, `endpoint`
- `authority`: `primary`, `official_aggregator`, `commercial_routing`, `curated`, `model_estimate`
- `retrieved_at`, `data_timestamp`, `freshness`
- `cache_expires_at`, `license_or_terms_note`
- `request_fingerprint`
- `usage_scope`: 어떤 필드의 근거인지

API 키, 인증 헤더, 개인정보와 전체 원본 응답은 사용자 JSON에 넣지 않는다.

## 5. 계획 갱신 정책

- 계획 생성 직후: 여행 날짜 기준 가능한 정적/지정시각 자료로 생성
- T-7일: 계절 운영시간·휴무·공사·노선 변경 확인
- T-1일: 영업시간·시간표 재확인
- 출발 1~2시간 전: 사용자가 원하면 실시간 도착과 교통상황으로 보정

재검증 전에도 일정은 계획안으로 유효하지만, 변경 가능성이 큰 값은 `needs_refresh: true`로 표시한다.
