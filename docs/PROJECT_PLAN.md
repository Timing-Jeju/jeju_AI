# 제주 전역·다일 여행 플래너 v0.7 구현 기준

## 제품 범위

하나의 결정론적 `ItineraryEvaluationEngine`을 중심으로 다음 여섯 MCP 도구를 제공한다.

- `recommend_jeju_day_trips`: 명시적 하루 시작·종료 경계 사이의 서로 다른 유효 일정 세 개 생성
- `evaluate_jeju_day_trip`: 활동-only 또는 전체 타임라인 사전 판정
- `revalidate_jeju_day_trip`: 현재 진행상태와 선택적 GPS 기반 남은 일정 재판정
- `search_jeju_places`: active publication 장소 검색
- `inspect_jeju_bus_stop`: 정류장·방향·canonical mapping 검사
- `preview_jeju_transfer`: 영속 저장 없는 문-to-문 이동 미리보기

공개 runtime 계약 버전은 `0.7.0`이다. v0.5·v0.6 입력은 schema validation에서 거부하며
`docs/examples/v0.6`은 당시 결과의 감사 자료로만 보존한다.
현재 운영 범위는 `JEJU_ALL/ALL`, 최대 5일이며 날짜별 숙소를 독립적으로 사용한다.

`activity_window`가 절대 시간 경계이고 선택적 `day_boundary.start_place/end_place`가 장소
경계다. 경계를 생략한 날짜만 숙소를 양쪽 endpoint로 사용한다. 첫날 terminal→숙소, 중간
날짜 숙소→숙소, 마지막 날 숙소→terminal, 당일 여행 terminal→terminal 입력을 손실 없이
받는다. `place_duration_preferences`의 사용자 체류시간은 전략별 조정이나 자동 수선으로
축소하지 않는다.

## 절대 불변조건

- Pydantic 모델이 공개 JSON Schema의 유일한 원본이다.
- 생성 성공은 `balanced`, `relaxed`, `experience_max`가 각각 하나인 정확히 세 추천이다.
- 세 유효 추천을 만들지 못하면 부분 결과 없이 `insufficient_feasible_routes`다.
- 필수 장소는 성공한 세 일정 모두에 포함한다.
- 모든 이동시간·거리·비용·운영시간은 존재하는 evidence fact로 추적한다.
- 버스는 confirmed 정류장 mapping, 서비스데이와 stop time, access/egress walk가
  모두 있어야 한다. 장소 입구가 없으면 TourAPI 대표좌표를 임시 endpoint로 사용하고
  검증 입구로 표시하지 않는다.
- 고정 수단과 사용자 이동 주장은 공식 근거와 별도로 검증하며 자동 변경하지 않는다.
- TMAP 원문·geometry와 사용자 원문·일정·GPS는 영속 저장하거나 로그에 남기지 않는다.
- 생성은 150초·외부 경로 40회, 판정은 30회, 실시간은 12회 예산을 넘지 않는다.
- 남은 일정은 현재 위치를 첫 이동 출발점으로 쓰되 최종 이동은 원 일정의 숙소로 복귀한다.

## 데이터 publication

외부 호출은 `config/data_sources.toml`의 승인된 source contract만 사용한다. 허용된 원본은
private object storage에 checksum 기반으로 보관한 뒤 정규화·품질 검증을 통과해야 active
snapshot이 된다. 수동 운영시간·입구·정류장 mapping·시간표도 같은 raw-first 흐름을 쓴다.
TourAPI의 `cat1/cat2/cat3`는 원문 분류로 보존하며 공식 `A05020900`만 카페로 판별한다.

정적 atomic capability는 제주 polygon, 장소, 운영시간, confirmed 정류장,
미래 버스, 요금 정책이다. 장소 추천은 `JEJU_ALL/ALL`의 active 장소·좌표를 사용하고,
정확한 버스 선택에만 여행일 service calendar·trip·stop time을 요구한다. TMAP/TAGO 실시간은
publication이 아니라 승인 계약·키·adapter 연결 상태로 별도 판정한다.
TourAPI 상세소개 운영시간은 전역 장소마다 조회 결과 또는 무응답 사유를 하나씩 보존한
`opening_hours_snapshot_ready=1.0`일 때 활성화한다. 날짜별 정확한 운영 판정률은 별도의
`opening_hours_ready` 품질지표로 관리한다. 정확한 시각이 없는 장소는 시각을 추정하지 않고
`UNVERIFIED` 주의 후보로 유지하며, 공식 휴무가 확인된 장소만 해당 날짜 후보에서 제외한다.
verified 입구 coverage는 일반 요청의 차단 조건이 아닌 품질 지표다. 이동보조 또는 계단
회피가 필수인 요청에만 verified 입구와 accessibility capability를 함께 요구한다.

제주 공식 시간표는 공공데이터포털의 이용허락범위 제한 없음 표시를 근거로 내부 원본
보관·정규화 publication에 승인됐다. 공개 재배포는 금지하며, BIS 시간표와 시행일 포함 변경
공지를 하나의 publication으로 검증한 뒤 사용한다.

## 결정론적 흐름

1. 장소 이름/ID를 active publication에서 유일하게 확정한다.
2. 운영시간·예외 휴무가 없으면 시각을 만들지 않고 `UNVERIFIED` 주의 후보로 사용한다.
   공식 휴무가 확인된 장소는 제외한다. verified 입구가 없으면 장소 fact의 대표좌표를
   `REPRESENTATIVE_PLACE_POINT`로 사용한다.
3. 좌표 하한은 후보 제거에만 쓰고 최종 이동 수치에는 쓰지 않는다.
4. 필요한 상위 구간만 TMAP 또는 공식 시간표로 지연 조회한다.
5. 운영시간, 입장마감, 도보, 환승, 이동비, 택시 호출 안전 버퍼, 휴식, 숙소 복귀 버퍼를 검증한다.
6. 하드 위반 후보를 제거한 뒤 세 전략을 점수화하고 관광지/순서/수단 다양성을 검사한다.
7. 정확 일정 수정안과 실시간 회복안은 원본을 바꾸지 않고 재검증된 안만 반환한다.

## 구현·승인 순서

1. v0.7 Pydantic 계약·Schema 동기화
2. 공통 판정·수정안 엔진
3. append-only publication과 복합 운영시간·시간표·정책 fact
4. TMAP 보행/차량, 공식 시간표 버스, 버스·택시 요금 범위와 런타임 조립
5. 세 전략 생성과 다양성·예산·timeout 검증
6. TAGO 실시간 도착 fact와 남은 일정 재판정
7. 제주 전역 실제 데이터 투입, coverage 측정, 권역별 PoC

품질 게이트는 한글 테스트 설명 검사 → Ruff → Pyright → offline pytest → Pydantic Schema
drift → synthetic example drift → checksum manifest → TMAP 메모리 전용 smoke →
Generate→Evaluate→Revalidate → 5일 이력 → 버스-only 전 구간 회귀 순서다. PostGIS/MinIO와
승인된 외부 API live 검증은 자격 증명과 당일 데이터가 있는 환경에서만 별도로 실행한다.

## MCP transport

로컬 `stdio`와 인증된 stateless Streamable HTTP `/mcp`는 같은 여섯 도구와 Pydantic 생성
schema를 노출한다. HTTP는 private network, TLS, 최대 5분 RS256 service JWT, local JWKS
issuer·audience·scope·expiry·JTI 검증을 모두 요구한다. launcher는 세 runtime secret과
bind/auth/JWKS/TLS 경로, 최소 시스템 환경만 자식 process에 남긴다. `/health`와 `/ready`는
provider 원문이나 secret을 반환하지 않는다.
