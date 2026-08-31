# 제주 데이터 1차 적재 전 체크리스트

이 문서는 초기 적재 속도를 유지하면서 잘못된 active snapshot만 차단하는 최소 절차다.
아래 순서에서 `--execute`가 없으면 로컬 계약 검증 또는 읽기 전용 측정만 수행한다.

## 1. 전체목록 API

장소·정류장·노선처럼 API 자체가 제주 전체목록을 반환하는 profile은 일반 refresh를 쓴다.

```text
jeju-data refresh --profile tourapi-jeju-places
jeju-data refresh --profile tago-jeju-bus-stops
jeju-data refresh --profile tago-jeju-bus-routes
```

검증 후 같은 명령에 `--execute`를 붙여 raw-first publication을 만든다.

## 2. 부분조회 API 집계

장소별 상세소개와 연도별 공휴일은 단건을 active snapshot으로 발행하지 않는다.
공휴일 CSV는 `solYear`, 장소 상세 CSV는 `contentId,contentTypeId` 헤더를 사용한다.

```text
jeju-data refresh --profile holiday-special-days --query-file holiday-years.csv
jeju-data refresh --profile tourapi-jeju-place-intro-hours --query-file place-intro-queries.csv
```

모든 행의 조회가 완료돼야 하나의 raw acquisition과 publication으로 활성화된다.
ZIP 원본의 `manifest.json`에는 API key 없이 조회 개수와 조회 범위가 기록되므로, query CSV
누락 여부를 publication 전에 대조한다.

대량 aggregate 조회는 첫 pagination 실패에서 `INCOMPLETE`로 멈춘다. 출력된 acquisition UUID와
동일한 query CSV·scope 확대 기준을 다음 실행의 `--resume-acquisition`에 전달하면, 계약 신선도 안의
완료 query는 private raw에서 재사용하고 누락 query만 호출한다. 신선도가 지났거나 manifest가
달라지면 재개하지 않고 실패한다. `HTTP_STATUS_429`는 즉시 재시도하지 않는다. API 호출 한도나
속도 제한이 회복된 뒤 가장 최근 acquisition UUID에서 재개한다.

## 3. 수동 검증 묶음

`docs/data-templates`의 헤더를 그대로 사용한다. 시간표는 기본 네 파일 외에 다음 예외 파일도
같은 묶음으로 입력한다.

- `service-calendar-exceptions.csv`
- `timetable-notices.csv`
- `route-service-exceptions.csv`

빈 예외 파일도 헤더는 유지한다. importer는 trip·service·노선·정류장·시각 순서를 묶음 내부에서
검증한다. 외부 공식 ID와의 전수 대조는 coverage 측정 및 권역별 PoC 단계에서 추가한다.

공식 XLSX raw ZIP을 직접 발행할 때는 `official-bus-timetable` 경로를 사용한다. ZIP에는
고정 checksum XLSX 두 파일과 `checksum-manifest.json`, `mapping-manifest.json`,
`service-day-manifest.json`이 모두 있어야 한다. mapping에는 유일 route pattern,
전체 route-stop 순서와 주요 정류장 stop time을, service-day manifest에는 공식 평일 출처와
공지 검토 기준일을 기록한다.

```text
jeju-data manual-import --dataset official-bus-timetable \
  --file private-official-timetable.zip --source-date 2026-08-14
jeju-data manual-import --dataset official-bus-timetable \
  --file private-official-timetable.zip --source-date 2026-08-14 --execute
```

이 builder는 평일 날짜만 받고 공휴일 calendar를 생성하지 않는다. raw ZIP은 정규화 전에
private object storage에 먼저 등록된다.

## 4. 제주 경계와 요금정책

제주 경계는 공식 SGIS ZIP을 변환하지 않은 채 직접 입력한다. importer가 고정 checksum,
유일한 `bnd_sido_00_<연도>_<분기>` SHP 묶음, UTF-8 CPG, EPSG:5179 PRJ,
`SIDO_CD=39`, `SIDO_NM=제주특별자치도`, `BASE_DATE=20250630`을 검증한다. 전국 CSV와
시군구·읍면동 SHP는 raw 보존 외에는 읽거나 projection하지 않는다.

```text
jeju-data manual-import --dataset jeju-boundary --file sgis-boundary.zip \
  --source-date 2025-06-30
jeju-data manual-import --dataset jeju-boundary --file sgis-boundary.zip \
  --source-date 2025-06-30 --execute
jeju-data manual-import --dataset taxi-fare-policy --file policy.toml --source-date YYYY-MM-DD
jeju-data manual-import --dataset bus-fare-policy --file policy.toml --source-date YYYY-MM-DD
```

경계 `--execute`는 publication UUID를 출력하고 같은 publication의 `service_area_ready`를 직접
측정해 `JEJU_ALL/ALL`, coverage `1.0`, blocking reason 없음으로 함께 발행한다.

## 5. Coverage 측정

초기 핵심 capability는 projection에서 직접 측정한다.

```text
jeju-data measure-coverage --publication UUID --capability place_search_ready
jeju-data measure-coverage --publication UUID --capability opening_hours_snapshot_ready
jeju-data measure-coverage --publication UUID --capability opening_hours_ready
jeju-data measure-coverage --publication UUID --capability future_bus_planning_ready \
  --service-date-from YYYY-MM-DD --service-date-to YYYY-MM-DD
```

경계 외 측정 결과를 확인한 뒤 `--execute`로 저장한다. `service_area_ready`는 경계 importer가
자동 측정·발행하며, `measure-coverage`로 재확인할 수 있다. 기존 `publish-coverage`는 호환
목적으로 남지만 capability와 무관한 source publication에는 발행할 수 없다.

TourAPI 상세소개는 모든 active 장소에 `STRUCTURED`, `PARTIAL`, `UNVERIFIABLE`, `NO_DATA` 중
정확히 하나의 관측 fact가 있어야 `opening_hours_snapshot_ready=1.0`으로 활성화된다. 활성화 후
날짜별 `opening_hours_ready`가 1.0 미만이면 `--extend-active --execute`로 품질지표를 추가한다.
이 값은 정확한 운영시간 보유율이며 활성화를 되돌리지 않는다. `UNVERIFIED`·`NO_DATA` 장소는
시각을 만들지 않고 주의 후보로 남고, 공식 `CLOSED` 또는 주간 휴무 근거가 있는 장소만 제외한다.

## 6. 승인 순서

1. 한글 테스트 설명 검사
2. Ruff
3. Pyright
4. offline pytest
5. PostGIS migration 및 빈 projection smoke
6. MinIO private bucket 생성 및 object versioning 활성화
7. MinIO raw-first smoke
8. 승인된 공식 API 소량 PoC
9. 제주 전역 1차 적재
10. 권역별 coverage 측정 및 버스 ID 매핑 검토

운영시간의 계절·일출 문구는 자동 `VERIFIED`가 아니라 검토 대상으로 남는다. 이 경고는 전체
수집을 중단시키지 않으며, 해당 장소만 수동 운영시간 publication으로 보완한다.
MinIO HEAD에서 checksum·길이뿐 아니라 version ID가 확인되지 않으면 acquisition을 등록하지 않는다.
