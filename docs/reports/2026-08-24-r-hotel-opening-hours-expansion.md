# 제주알호텔 버스 후보 운영시간 확대 (2026-08-24)

## 결론

실제 라이브 버스 요청과 같은 조건으로 복원한 primary·fallback 후보 합집합은 9곳이다.
기존 active 운영시간이 있던 4곳을 제외한 5곳을 승인된 `tourapi.place-intro`로
raw-first 재조회했다. 공식 상세행이 반환된 4곳은 모두 구조화됐지만, 2025 제주 반려동물
문화산업 한마당은 상세행이 반환되지 않았다. 따라서 후보 확대 범위는 4/5이며
`coverage=1.0`이 아니므로 새 publication을 활성화하지 않았다.

## bounded 대상

조회 입력은
[`tourapi-intro-queries.csv`](../manifests/r-hotel-hours-2026-08-24/tourapi-intro-queries.csv)에
고정했다. 키·응답 원문·사용자 원문·GPS는 문서나 로그에 남기지 않았다.

| 장소 | 기존 active | v7 정규화 결과 |
|---|---|---|
| 보성시장 | `UNVERIFIED` | `VERIFIED`, 매일 09:00~20:00 |
| 제주성지 | `UNVERIFIED` | `VERIFIED`, 24시간 |
| 그옛맛 | `UNVERIFIED` | `VERIFIED`, 월~금 09:00~17:00, 마지막 주문 16:10 |
| 라토커피제주 | `UNVERIFIED` | `VERIFIED`, 매일 11:00~23:00, 마지막 주문 22:00 |
| 2025 제주 반려동물 문화산업 한마당 | `UNVERIFIED` | 공식 상세행 없음 |

행사가 2026-08-24에 폐쇄됐다고 추정하거나 장소 fact를 삭제하지 않았다. 현재 승인 계약만으로
적용일을 검증할 수 없으므로 계속 `UNVERIFIED`로 둔다.

## 정규화 보강

실제 반환 구조로 두 누락을 RED 테스트부터 고정했다.

1. 휴무일 필드가 정확히 `주말`이면 라벨 없는 운영시간을 월~금 규칙으로만 변환한다.
2. 쇼핑 콘텐츠의 공식 `opentime` 필드를 운영시간 후보 필드에 포함한다.

휴무 근거가 없거나 계절·일출·다중 무라벨 구간처럼 모호한 값은 계속 거부한다. 변경된
정규화 의미를 append-only acquisition과 구분하기 위해 승인 계약의 스키마 버전을
`tour-place-intro-hours-v7`로 올렸다.

## raw-first 결과

| 항목 | 결과 |
|---|---|
| source preflight | `PASS`, 승인 계약·허용 host/path·secret 이름 검증 |
| query 수 | 5 |
| 공식 상세 raw 행 | 4 |
| 정규화 규칙 | 26 |
| 반환행 중 거부 | 0 |
| publication | `a78260cb-b1c7-4885-b717-5c10d7517d6c` |
| dataset | `2026-08-24-010272fda530` |
| 상태 | `STAGED`, active 아님 |
| 후보 대상 coverage | 4/5 (`0.8`) |

기존 `JEJU_EAST/POC_V1`은 별도 7곳 범위이므로 이 publication으로 측정한 coverage는
0이다. 서로 다른 범위를 합쳐 1.0으로 가장하지 않았다. 새 후보 범위도 4/5이므로
`publish_coverage`를 실행하지 않았다.

## 재인수 판단

active pointer가 바뀌지 않아 공개 Generate·Evaluate 결과도 바뀌지 않는다. 같은 상태에서
TMAP을 다시 호출하면 API 사용량만 늘어나므로 이번 단계에서는 재호출하지 않았다. 다음
활성화 조건은 행사 항목의 2026-08-24 적용 가능성을 승인된 공식 fact로 검증하거나,
날짜 유효성 근거가 없는 행사 콘텐츠를 자동 발견 후보에서 제외하는 별도 안전 정책을
계약·RED 테스트로 확정하는 것이다. 그 뒤 정확한 후보 분모에서 1.0을 측정하고 한 번만
Generate→Evaluate를 재인수한다.

## 보호 조건

- 승인되지 않은 외부 source는 호출하지 않았다.
- TourAPI raw는 private versioned object storage에 먼저 저장하고 checksum·길이·version을
  검증한 같은 acquisition에서만 정규화했다.
- TMAP 원문·geometry와 사용자 원문은 수집·저장·출력하지 않았다.
- 운영시간을 추정하거나 부분 coverage를 active로 승격하지 않았다.
- 공개 JSON 계약과 추천 세 개 또는 `insufficient_feasible_routes` 규칙은 변경하지 않았다.

## 품질 게이트

- 모든 Python 테스트의 한글 목적 설명: `Pass`
- Ruff: `Pass`
- Pyright: `0 errors, 0 warnings`
- 전체 offline pytest: `409 passed, 9 skipped`
- v0.6 checksum manifest: `Pass`
