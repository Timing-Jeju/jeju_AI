# 제주 데이터 원천 1차 최종 리뷰

검토 기준일: 2026-08-10

## 결론

현재 핵심 원천 조합은 제주 하루 일정 생성 목적에 적합하다. 하나의 공급자에 모든 판정을
맡기지 않고, 제주 공식 정적 시간표·국가 교통 ID/실시간·관광 POI·문간 경로를 역할별로
분리한 것이 가장 안전하다. 공중화장실, 날씨, VisitJeju 보강, TMAP 대중교통 경로는 현재
결정 엔진이 소비하지 않으므로 1차 적재에서 제외한다.

| 목적 | 1순위 원천 | 판정 | 보완 원천 |
|---|---|---|---|
| 관광지·숙박·음식점 후보 | 한국관광공사 TourAPI | 제주 전역 탐색에 가장 적합한 공식 집계 | 시설 직접 공지·수동 검증 운영시간 |
| 장소 운영시간 | 시설 직접 공식 정보, 없으면 TourAPI 상세소개 | TourAPI 원문은 해석 불가·계절 문구가 있어 단독 100% 근거가 아님 | `travel.place-hours-map` |
| 버스 정적 시간표 | 제주버스정보시스템 | 제주 미래 버스 판정의 1순위 | 시행일이 있는 변경공지와 첨부 시간표 |
| 정류장·노선 ID | TAGO 정류소·노선 API | 전국 표준 provider ID 확보에 적합 | 제주 BIS 정류장 번호와 confirmed mapping |
| 실시간 버스 도착 | TAGO 버스도착정보 | 정류소 기준 현재 운행 도착정보에 적합 | 제주 coverage PoC가 통과한 ID만 사용 |
| 보행·차량 문간 경로 | TMAP 보행자·자동차 | 실제 도로망 경로에 적합 | 미래 차량은 현재 snapshot proxy로 명시 |
| 공휴일 | 한국천문연구원 특일정보 | 공휴일 fact에 적합 | 버스 service calendar와 반드시 결합 |
| 택시·버스 요금 | 제주버스정보시스템 공식 요금표 | 현재 요율 근거로 적합 | 경로 fact와 계산 fact를 분리 |
| 제주 서비스 경계 | SGIS 행정구역 경계 | 최종 제주 포함 판정에 적합 | 사각형 좌표는 후보 제거에만 사용 |

## 이번 검토에서 확인한 핵심 사실

- TourAPI는 관광정보·소개·이미지 등 약 26만 건의 전국 관광정보를 제공하므로 제주 후보
  discovery에 적합하지만, 개별 시설의 당일 영업 보장은 아니다.
- 제주 BIS는 2026년에도 시행일을 명시한 노선·시간표 변경과 첨부파일을 계속 게시한다.
  따라서 정적 시간표 화면만 수집해서는 부족하고 변경공지 publication을 함께 적용해야 한다.
- 제주 공식 버스 요금표는 간·지선 성인 1,150~1,200원, 어린이 350~400원이며, 급행은
  성인 최대 3,000원·어린이 최대 1,500원의 거리비례 범위다.
- TAGO 버스도착 API는 현재 운행 버스의 정류소별 도착예정정보를 제공한다. 응답 없음은
  미운행 증거가 아니며 제주 정류장/노선 ID mapping PoC가 선행돼야 한다.
- 일반 TMAP 자동차 응답은 조회시점 교통이다. 미래 일정에서도 사용할 수는 있으나
  `current_snapshot_proxy_for_future` 추정 근거로만 취급한다.

## 적재 전 남은 승인 사항

1. 제주 BIS 시간표·첨부파일의 저장 및 내부 파생 이용조건을 승인하고 source fingerprint를 확정한다.
2. SGIS 제주 경계의 변환·저장 이용조건을 승인한다.
3. 제주 전체 TourAPI 장소 목록으로 상세소개 query manifest를 생성하고 누락·중복을 대조한다.
4. 제주 권역별 TAGO 정류장·노선 coverage와 BIS 정류장 번호 mapping을 실데이터로 측정한다.
5. 공식 정책 TOML과 active DB 요율의 모든 핵심 값이 일치한 경우에만 publication ID를 evidence에 연결한다.

## 과도한 초기 차단을 피하는 운영 원칙

- 운영시간을 해석하지 못한 행은 전체 수집을 실패시키지 않고 해당 장소 coverage gap으로 남긴다.
- 미래 자동차 경로는 버리지 않고 추정 근거와 낮은 confidence로 반환한다.
- `PENDING` 수동 원천도 `--execute`가 없으면 로컬 dry-run을 허용한다.
- checksum·길이·object version, 시간표 참조 무결성, 미지 evidence ID처럼 결과를 오염시키는
  명백한 오류만 publication 전에 강제 차단한다.

## 공식 근거

- TourAPI: https://www.data.go.kr/data/15101578/openapi.do
- 제주 공식 시간표·요금: https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule
- 제주 변경공지: https://bus.jeju.go.kr/notice/list
- TAGO 버스도착정보: https://www.data.go.kr/data/15098530/openapi.do
- TMAP API: https://tmapapi.tmapmobility.com/
- 제주 택시요금: https://bus.jeju.go.kr/publicTrafficInformation/taxiInfo
# v0.5 동부 PoC 계약 재검토 (2026-08-11)

| source ID | 변경/신규 | 실제 raw 형식 | contract fingerprint | terms fingerprint |
|---|---|---|---|---|
| `tago.bus-route-stops` | 신규 | JSON | `c9e0e39221d7e46ea484e26864f1dc073a0ff4d6db0dba1599dac27c8428284c` | `7eb307c0c724e75446c8e603ea50609122674a387eff8db1c1614bad594c9c13` |
| `travel.service-scope-manifest` | 신규 | ZIP | `5bc74b8719b5a8c70fa681c163dec3888a51c928f4b733e2e76b90b7004a099f` | `internal-v1` |
| `travel.place-entrance-map` | 형식 보정 | CSV | `f511f37378f0bb3fe183f9670bf93d953ebe3ec518c82b17845ace2f49e43c46` | `internal-v1` |
| `jeju.bus-timetable` | XLSX 묶음 직접 수집 | ZIP | `168d86aef557eb9dc217d610666579f2ad14b068e8a909672bbc26c2a2489281` | `6c72d40ca1ac50ac0268176db949e8d5d68c7dc67b4b93c52603d41fad16ea1e` |

TAGO route-stop은 기존 노선 source를 대체하지 않는 별도 snapshot이다. scope·입구는
행별 source reference를 요구한다. 장소 가격은 제품 범위에서 제외하고 수집하지 않는다. 시간표는 공식 XLSX, checksum manifest, mapping manifest,
service-day source reference를 하나의 private raw ZIP으로 보존하며 가공 CSV는 승인 원천이 아니다.
광복절 적용 근거가 없으면 `BUS_SERVICE_DAY_UNVERIFIED`로 중단한다.

이미 적용된 `0005_v05_east_poc.sql`의 가격 테이블은 migration 이력을 수정하지 않기 위해
물리적으로 남겨두되, source catalog·normalizer·runtime 조회·capability coverage에서는 제거했다.
따라서 새 가격 raw를 받거나 projection을 발행하는 운영 경로는 없다.
