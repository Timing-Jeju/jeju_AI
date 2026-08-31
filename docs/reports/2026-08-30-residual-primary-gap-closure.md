# 2026-08-30 잔여 1차 공백 순차 처리 결과

## 결론

1차 안전 구현 공백은 모두 닫혔다. 버스·영업시간·입구·접근성·식이 제약은 이제 근거가
부족할 때 추정 성공하지 않고 각각 측정된 coverage 또는 exact fact 부재로 닫힌다. 다만 제주
전역의 실제 데이터가 모두 확보됐다는 뜻은 아니다. 공식 원본으로 유일하게 결합할 수 없는
버스 노선, 구조화할 수 없는 영업시간, 아직 발행하지 못한 검증 입구·메뉴 fact는 계속 미완료다.

## 활성 데이터 재측정

기준 시각은 2026-08-30 KST이며, 활성 PostGIS projection만 집계했다.

| 항목 | 현재 결과 | 운영 판단 |
|---|---:|---|
| TourAPI 활성 장소 | 992 | 제주 전역 장소 검색 가능 |
| 영업시간 관측 완료 | 1,019 / 1,019 | snapshot completeness 1.0 |
| 검증 영업시간 장소 | 634 | exact completeness는 부분 |
| 2026-08-31 제주 전역 영업시간 | 0.667380 | 전역 exact gate 미충족 |
| 검증 BREAK 규칙 | 108 | 생성·평가에서 실제 운영창 차감 |
| confirmed 정류장 identity | 4,273 | stop identity gate 충족 |
| 2026-08-31 활성 scheduled trip | 2,973 | exact인 운행만 활성 |
| 2026-08-31 제주 전역 버스 route coverage | 0.383984 | 전역 future bus gate 미충족 |
| 현재 검증 입구 | 0 | 일반 요청은 대표 장소점임을 명시, 이동약자 요청은 fail-closed |
| 접근 가능한 도보 입구 | 0 | accessibility gate 미활성 |
| 현재 검증 식이 메뉴 fact | 0 | 알레르기·제외음식 식당 추천은 fail-closed |

공식 평일 workbook exact 감사에서는 5,723개 후보 행 중 2,793개를 유일하게 결합했다.
기존 2,751개보다 42개 늘었으며, 정규화 후 checkpoint 이름이 유일하게 완전 일치하는 경우만
추가했다. TAGO 활성 catalog는 974개 pattern, 247개 노선번호로 다시 확인했다. 남은 동률과
catalog 불일치는 방향을 추정하지 않았다. 동부 PoC와 R호텔 범위의 2026-08-31 버스 coverage는
각각 1.0으로 유지된다.

## 순차 처리 내용

### 1. exact 버스 결합

- endpoint·checkpoint 수가 같은 복수 pattern에서 정규화 이름 완전 일치 수가 양수이며
  유일한 경우만 선택하도록 했다.
- 고정 checksum의 owner-only 공식 workbook에서 새 raw ZIP을 먼저 만들고 append-only
  publication으로 발행했다.
- 제주 전역 0.383984를 1.0으로 올리거나 전역 capability를 활성화하지 않았다. unresolved
  workbook 행을 추정 결합하면 정류장 시각과 경로 근거가 함께 오염되기 때문이다.

### 2. 영업시간과 휴게시간

- 장소 전체의 상세소개 관측은 완료됐지만, 빈 값·계절 문구·복수 비표준 범위는 `unknown`으로
  보존했다.
- 생성 gateway가 첫 OPEN 행만 선택하던 결함을 제거했다. 같은 날의 모든 검증 OPEN/BREAK를
  읽고 BREAK를 뺀 뒤 체류 전체를 담는 다음 OPEN 구간을 고른다.
- Evaluate도 같은 구간과 fact ID로 판정하며 특별 영업시간에도 검증 BREAK를 적용한다.
- 따라서 snapshot 1.0과 exact 0.667380을 서로 다른 capability로 유지한다.

### 3. 검증 입구와 접근성

- 제주 전역 분모를 과거 scope manifest가 아니라 현재 활성 TourAPI 장소 전체로 바꿨다.
- 접근성은 `entrance_type=accessible`, 도보 지원, 현재 유효한 `VERIFIED` fact만 센다.
- 수기 coverage 발행을 막고 재현 가능한 측정만 허용한다.
- 공식 제주 무장애 파일은 주변 시설·접근성 정보 중심이며 검증 entrance point 좌표 계약이
  아니므로 입구 fact로 변환하지 않았다. [공공데이터포털 공식 설명](https://www.data.go.kr/data/15075714/fileData.do?recommendDataYn=Y)

### 4. 알레르기·제외음식

- `travel.restaurant-dietary-map`만 식당 안전성 근거가 될 수 있도록 source binding을 고정했다.
- Pydantic 정규화, append-only migration, raw-first 수동 import, active view, coverage 측정,
  Generate/Evaluate/Revalidate exact 배열 포함 검사를 연결했다.
- 서로 다른 현재 유효 식당 세 곳의 명시적 메뉴 fact가 없으면 capability를 열지 않는다.
- 농촌진흥청 메뉴젠 API는 음식·재료·알레르기 일반 정보를 제공하지만 TourAPI 식당과 실제 메뉴를
  유일하게 연결하지 않으므로 현재 식당 안전성 fact로 사용하지 않았다.
  [공공데이터포털 공식 API 설명](https://www.data.go.kr/data/15143502/openapi.do?recommendDataYn=Y)

### 5. 외부 종단과 인수 fixture

- TourAPI place와 TAGO route catalog는 2026-08-30 실제 API refresh publication이 활성이다.
- TourAPI, TAGO arrival, TMAP pedestrian/driving의 승인 계약·secret preflight가 모두 통과했다.
- TMAP 대표 권역 smoke는 최신 active snapshot에서 anchor 최근접 장소를 선택하도록 바꿨다.
  메모리 전용 실제 호출 3/3이 통과했고 원본·geometry·수치는 영속 저장하지 않았다.
- 검증 입구가 0개이므로 이 smoke는 연결성 인수일 뿐 `driving_routing_ready` 또는 접근성
  활성화 근거가 아니다.

## 2026-08-31 당일 TAGO 잔여 조건

선택 버스의 당일 도착정보는 여행일이 되기 전에 성공했다고 기록할 수 없다. 기존 8월 31일
버스 전용 Generate→Evaluate→정적 Revalidate는 exact 시간표로 통과했지만, `tago.bus-arrival`은
8월 31일 선택 승차 정류장에 한해 memory-only로 조회하고 같은 일정의 Revalidate를 다시
통과해야 종단 인수가 끝난다. 호출 원문과 정류장별 응답은 저장하지 않는다.

## 품질·보안 검증

- 모든 Python 테스트의 한글 목적 설명: Pass
- Ruff: Pass
- Pyright: 0 errors, 0 warnings
- 전체 offline pytest: 494 passed, 9 skipped
- 격리 PostGIS·MinIO integration: 25 passed, 0 skipped
- Pydantic schema·synthetic example·v0.6 checksum drift: Pass
- 개발 DB migration: 18개 적용, `restaurant_dietary_fact` active view 행은 0
- TMAP/TAGO 원본·상세 geometry·사용자 원문·GPS·secret 값 저장 또는 로그 출력 없음

최종 판단은 “1차 안전 구현 완료, 전역 데이터 완성 미완료, 8월 31일 당일 TAGO 종단 대기”다.
미완료 데이터는 추천을 억지로 세 개 만들지 않으며 세 개가 성립하지 않으면 추천 0개와
`insufficient_feasible_routes`를 반환한다.
