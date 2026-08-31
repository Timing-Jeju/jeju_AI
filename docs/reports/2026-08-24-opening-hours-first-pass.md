# 동부 PoC 운영시간 1차 결과 (2026-08-24)

## 결론

판정은 `Scoped Pass` 다. 출입구·접근성은 작업 범위에서 제외했고, active
출입구는 0건으로 유지했다. `JEJU_EAST/POC_V1`의 숙소를 제외한 필수 활동
장소 7곳에 대해 2026-08-24부터 2026-08-30까지의 운영시간 근거를 active로
발행했다. 제주 전역이나 이 날짜 밖의 coverage 1.0은 주장하지 않는다.

## raw-first 수집과 First RED

승인된 `tourapi.place-intro` 프로필로 체크인된 13개 동부 후보를 조회했다.
13개 쿼리의 pagination은 모두 완료됐고 12개 원본 행을 private object storage에
먼저 보관했다. 제주레일바이크 1곳은 TourAPI가 행을 반환하지 않았다.

첫 정규화는 정상 행 0건으로 `NORMALIZED_RECORDS_EMPTY`에서 차단됐다. 원인은
운영시간 문장과 휴무 필드가 분리된 TourAPI 형식과 `상시개방`을 현재 파서가
지원하지 않았기 때문이다. 한글 목적 RED를 먼저 실패시킨 뒤 다음만 구조화했다.

- 휴무 필드가 명시적으로 `연중무휴`인 무요일 시간 구간
- `상시 이용 가능`, `연중 이용 가능`, `상시개방`과 연중무휴가 함께 있는 경우
- HTML 줄바꿈과 명시적 라스트 오더

휴무 근거가 없는 무요일 구간, 의미 라벨이 없는 복수 구간, 계절·일출·일몰
시간은 계속 거부한다. 당시 정규화 스키마 `tour-place-intro-hours-v5`로 재수집한 결과는
다음과 같다.

| 항목 | 결과 |
|---|---:|
| raw 행 | 12 |
| 구조화 가능 장소 | 6 |
| 정규화 규칙 | 42 |
| 거부 행 | 6 |
| 필수 분모 coverage | 5/7 (`0.714286`) |
| staged publication | `957abb24-1995-4f18-9100-c5113d9d00e9` |

TourAPI publication은 1.0이 아니므로 활성화하지 않았다.

## 정확 범위 수동 발행

TourAPI 비공개 원본에서 확인한 필수 6곳과, 현재 비짓제주 공식 상세의
09:00~17:30·연중무휴 표시를 확인한 제주레일바이크를 하나의 curated 묶음으로
구성했다. 성산일출봉의 계절 구간과 첫째 월요일 휴무를 대상 주간에 대조했고,
구조적으로 상시개방이 확인된 장소만 00:00~익일 00:00로 발행했다.

| 항목 | 결과 |
|---|---:|
| active source | `travel.place-hours-map` |
| publication | `e7529e38-f2cb-480e-b6e1-24cf5979afab` |
| dataset | `2026-08-24-52ec92edc24c` |
| 정상/거부 | 7/0 |
| active 장소 규칙 | 7곳 7건 |
| 날짜별 coverage | 2026-08-24~30, 매일 7/7 (`1.0`) |

수동 묶음도 CSV ZIP을 private raw에 먼저 보관한 뒤 staging과 coverage 1.0을
거쳐 원자적으로 활성화했다.

## 런타임 검증과 제한

수동 source의 `source_date`를 신선도 근거로 읽지 못하던 간격을 RED로 재현하고,
API source의 `observed_at`이 없을 때만 계약에서 선언한 `source_date`를 사용하도록
생성·판정 경로를 수정했다. 미래 날짜와 8일을 넘긴 근거는 계속 거부한다.

운영 DB를 직접 읽은 결과, 생성 gateway는 7/7을 `VERIFIED`로 불러왔고 판정
evidence도 7개 시간창과 7개 opening fact를 생성했다. 다만 전체 Generate 라이브
재실행은 한 번은 `ROUTING_BUDGET_EXHAUSTED`, 추가 관광지 탐색을 끈 정확 범위
요청에서는 `insufficient_feasible_routes`로 종료됐다. 성공 추천 세 개를 만들지 못했으므로
부분 추천을 반환하지 않았다.

따라서 이 문서의 `Scoped Pass`는 운영시간 데이터·coverage·런타임 소비 경로에
한정된다. 기존 버스-only 세 추천에 운영시간이 함께 반영된 최종 라이브 인수는
라우팅 후보·예산 경로를 별도로 검토한 후 다시 수행해야 한다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 전체 offline pytest: `405 passed, 9 skipped` (414 collected)
- v0.6 checksum manifest: `Pass`

## 보호 조건

- 출입구·접근성 코드와 데이터는 변경하지 않았다.
- 추정 시간, 제3자 영업시간, TourAPI 빈 행을 대체한 가상 fact를 만들지 않았다.
- TourAPI 원문은 private raw에만 보관했고 보고서에 payload를 복사하지 않았다.
- TMAP 원문·geometry, 사용자 원문·GPS를 저장하거나 출력하지 않았다.
- 정확한 추천 세 개를 만들지 못한 라이브 실행은 전체 실패로 닫혔다.
