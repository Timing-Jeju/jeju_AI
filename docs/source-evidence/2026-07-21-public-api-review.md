# 공공 API source evidence 검토

검토일: 2026-07-21

이 문서는 당시 검토 기록이다. 2026-08-10 핵심 범위 재심사에서 TMAP 대중교통과
공중화장실은 현재 엔진이 소비하지 않는 데이터로 분류되어 승인 catalog에서 제거됐다.

## 판정 방법

공식 데이터 상세 페이지의 제공기관, endpoint, 수정일, 승인 절차, 이용허락범위를 확인했다. `terms_fingerprint`는 아래 canonical claim 문자열의 SHA-256이다. 원문 웹페이지 전체를 저장한 checksum이 아니다.

`raw_private_storage_allowed=true`는 “이용허락범위 제한 없음”에서 private 내부 원본 보관이 허용된다고 해석한 프로젝트 판정이다. 공개 재배포는 별도 이미지·제3자 권리 검토 전까지 `false`로 유지한다.

## TourAPI 장소

- 공식 상세: https://www.data.go.kr/data/15101578/openapi.do
- 제공기관: 한국관광공사
- Base URL: `apis.data.go.kr/B551011/KorService2`
- 공식 페이지 수정일: 2026-02-26
- 업데이트 주기: 실시간
- 이용허락범위: 제한 없음
- 주의: 이미지에는 공공누리 유형 및 별도 사용 제한이 있으므로 1차 projection은 이미지 재배포를 하지 않는다.
- canonical claim: `https://www.data.go.kr/data/15101578/openapi.do|수정일=2026-02-26|이용허락범위=제한없음|raw_private_storage=inferred-allowed|reviewed=2026-07-21`
- fingerprint: `db6b27b7cd360c5710b9bf2d4c92f5ac7682acc70b7204cc02d48fe3dad070b6`

## TAGO 버스정류소

- 공식 상세: https://www.data.go.kr/data/15098534/openapi.do
- 제공기관: 국토교통부
- 서비스 URL: `apis.data.go.kr/1613000/BusSttnInfoInqireService`
- 공식 페이지 수정일: 2023-07-12
- 개발·운영 자동승인, 이용허락범위 제한 없음
- 좌표 기반 조회 결과 필드: `gpslati`, `gpslong`, `nodeid`, `nodenm`, `citycode`
- canonical claim: `https://www.data.go.kr/data/15098534/openapi.do|수정일=2023-07-12|이용허락범위=제한없음|raw_private_storage=inferred-allowed|reviewed=2026-07-21`
- fingerprint: `ad7c38b21b42be1609a5b8e52ce4529e7695441eb603292a155edc6f1c48b2c4`

## TAGO 버스노선

- 공식 상세: https://www.data.go.kr/data/15098529/openapi.do
- 제공기관: 국토교통부
- 서비스 URL: `apis.data.go.kr/1613000/BusRouteInfoInqireService`
- 공식 페이지 수정일: 2025-06-16
- 전국 범위, 실시간 갱신, 개발·운영 자동승인, 이용허락범위 제한 없음
- 노선 기본 필드: `routeid`, `routeno`, `startnodenm`, `endnodenm`, 첫차·막차·요일별 배차간격
- canonical claim: `https://www.data.go.kr/data/15098529/openapi.do|수정일=2025-06-16|이용허락범위=제한없음|raw_private_storage=inferred-allowed|reviewed=2026-07-21`
- fingerprint: `7eb307c0c724e75446c8e603ea50609122674a387eff8db1c1614bad594c9c13`

## TMAP 대중교통

- 공식 약관: https://transit.tmapmobility.com/terms
- 무료체험: 대중교통 10건/일, 요약정보 10건/일
- 제한: 취득 데이터는 저장 후 24시간 이상 사용할 수 없음
- 프로젝트 정책: 영속 저장 금지, 프로세스 메모리 TTL 최대 23시간 50분
- canonical claim: `https://transit.tmapmobility.com/terms|24시간이상사용금지|무료체험=10건/일|reviewed=2026-07-21`
- fingerprint: `08e65d4416e916e8d96eb66e55eab6b3ec8b884040c848d6512ecdb2fd1d7f32`

## 당시 PENDING

- 제주 공식 버스 시간표: 2026-07-21 당시 다운로드 경로, service-day 의미, 저장·파생 허가 미확정
- 공휴일 source 상세와 이용허락 evidence 추가 검토 필요
- 실제 성공 추천은 위 시간표와 verified entrance/confirmed stop mapping publication 전까지 비활성화한다.

## 2026-08-10 추가 검토

### 한국천문연구원 특일 정보

- 공식 상세: https://www.data.go.kr/data/15012690/openapi.do
- 공식 응답 형식: XML
- canonical claim: `https://www.data.go.kr/data/15012690/openapi.do|provider=한국천문연구원|format=XML|reviewed=2026-08-10`
- fingerprint: `b336a5fae898f31298e85d3d7bbf4a00cb1b8d64ca29a9729fc715c49f830e20`

### TAGO 버스 도착정보

- 공식 상세: https://www.data.go.kr/tcs/dss/selectApiDataDetailView.do?publicDataPk=15098530
- 핵심 필드: 정류장·노선 ID, 노선 번호, 남은 정류장 수, `arrtime`(초)
- canonical claim: `https://www.data.go.kr/tcs/dss/selectApiDataDetailView.do?publicDataPk=15098530|provider=국토교통부|arrtime=seconds|reviewed=2026-08-10`
- fingerprint: `fd8921839c03519c7bddf7537b0c6e302ca6140b0b04f4fde4d3e6242054201d`

### TMAP 보행·자동차 경로

- 공식 약관: https://tmapapi.tmapmobility.com/terms.html
- 프로젝트 정책: 원문과 geometry 영속 저장 금지, 목적별 메모리 TTL만 허용
- 보행 canonical claim: `https://tmapapi.tmapmobility.com/terms.html|pedestrian|memory-only-under-24h|reviewed=2026-08-10`
- 보행 fingerprint: `70894886c3907d9a5c0b4021ddf42d0c1d026f430e14ab7b8c6a363c446a4420`
- 자동차 canonical claim: `https://tmapapi.tmapmobility.com/terms.html|driving|memory-only-under-24h|reviewed=2026-08-10`
- 자동차 fingerprint: `e4cc7265adbd57a2a5ab9328bf344575e091a6cb7353ae4c4a04b00bb7292f2b`

### 제주 택시 운임

- 공식 안내: https://bus.jeju.go.kr/publicTrafficInformation/taxiInfo
- 적용일: 2024-07-01
- canonical claim: `https://bus.jeju.go.kr/publicTrafficInformation/taxiInfo|effective=2024-07-01|reviewed=2026-08-10`
- fingerprint: `78d1bc6e8c12ef9fef25e0cd4fa74e05dd4ea6ec4e38ea204fdc10b85bced819`

### 제주 공식 버스 시간표 재검토

- 공식 시간표 화면: https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule
- 화면이 사용하는 공식 목록 endpoint: `POST /publicTrafficInformation/getBusRouteNum`
- 선택 노선 검증 endpoint: `POST /data/schedule/getGroupScheduleInfo`
- 공식 Excel 다운로드 endpoint: `GET /data/schedule/downScheduleExcel?gscheduleId=...`
- 변경 공지: https://bus.jeju.go.kr/notice/list
- 확인된 사실: 공식 화면은 노선별 시간표 파일을 제공하고, 공지는 시행일이 있는 변경을
  계속 게시한다.
- 미확정 사항: 시간표 파일의 내부 재사용·파생 저장 허가를 명시한 이용조건과 안정적인
  기계 수집 계약을 찾지 못했다.
- 판정: `jeju.bus-timetable`은 계속 `PENDING`이다. 운영자가 이용조건을 확인하고
  source contract를 승인하기 전에는 다운로드·저장·publication을 실행하지 않는다.

### 2026-08-11 승인 갱신

- 공공데이터포털 공식 상세: https://www.data.go.kr/data/3043887/fileData.do
- 제공기관: 제주특별자치도
- 공식 페이지 수정일: 2025-07-30
- 제공형태: 제주 BIS에서 노선별 Excel 다운로드
- 이용허락범위: 제한 없음
- 프로젝트 판정: private 원본 보관·내부 정규화 허용, 공개 재배포는 별도 검토 전까지 금지
- canonical claim: `https://www.data.go.kr/data/3043887/fileData.do|수정일=2025-07-30|이용허락범위=제한없음|raw_private_storage=inferred-allowed|reviewed=2026-08-11`
- fingerprint: `6c72d40ca1ac50ac0268176db949e8d5d68c7dc67b4b93c52603d41fad16ea1e`
- 판정: `jeju.bus-timetable`을 내부 시간표 publication 용도로 `APPROVED`로 전환했다.

### 제주버스 요금 범위

- 공식 안내: https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule
- 2026-08-10 화면 기준 간·지선 카드/현금, 급행 기본·최대 요금 범위를 versioned 정책에
  저장했다.
- 도민 고령자 할인, 카드 보유, 실제 환승 할인은 사용자 정보 없이 가정하지 않는다.
- canonical claim: `https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule|fare-table-observed=2026-08-10|internal-policy-range|reviewed=2026-08-10`
- fingerprint: `bc9e5bb4c21f08d56d3f5b6acfe945fb9910b2720bd8169c2a2bd8e9f76d736d`
