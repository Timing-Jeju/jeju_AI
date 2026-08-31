# 제주 하루 여행 추천 MCP 문서

이 디렉터리는 PDF 제안서와 분리된 실제 제품 명세의 기준 문서다. 현재 제품 목표는 사용자의 제주 하루 여행 요청을 받아 이동과 운영시간을 검증한 서로 다른 추천 일정 3개를 상세 JSON으로 반환하는 것이다.

## 문서 구조

- [PROJECT_PLAN.md](./PROJECT_PLAN.md): 범위, 아키텍처, LangChain/MCP 처리 흐름, 개발 단계와 검증 기준
- [JSON_CONTRACT.md](./JSON_CONTRACT.md): 입력·출력 필드 의미, 시간 계산 규칙과 JSON 불변조건
- [IMPLEMENTATION_REVIEW.md](./IMPLEMENTATION_REVIEW.md): 과거 v0.2 리뷰와 현재 구현 경계
- [OFFICIAL_SOURCES.md](./OFFICIAL_SOURCES.md): 공식 데이터 출처, 필드별 우선순위, 제약과 보존 정책
- [contracts/day-trip-recommendations.schema.json](./contracts/day-trip-recommendations.schema.json): Pydantic에서 생성한 v0.6 출력 Schema
- [examples/v0.6/synthetic](./examples/v0.6/synthetic): Generate→Evaluate→Revalidate 합성 계약 예시
- [examples/v0.5/east-poc](./examples/v0.5/east-poc): 변환하지 않는 v0.5 보관 예시
- [REASON_CODES.md](./REASON_CODES.md): 안전한 실패·품질 reason code
- [reports/2026-08-17-v06-live-acceptance.md](./reports/2026-08-17-v06-live-acceptance.md): v0.6 순차 라이브 인수 결과와 남은 데이터 차단 조건
- [reports/2026-08-18-v06-consolidated-final-acceptance.md](./reports/2026-08-18-v06-consolidated-final-acceptance.md): request-scoped bus-only 3추천과 통합 1차 인수 결과
- [reports/2026-08-18-v06-transfer-candidate-acceptance.md](./reports/2026-08-18-v06-transfer-candidate-acceptance.md): 버스-only 1회 환승 후보 그래프의 DB-only 인수 결과
- [reports/2026-08-24-phase1-reacceptance.md](./reports/2026-08-24-phase1-reacceptance.md): 오늘자 공식 시간표와 선택 버스 same-day TAGO까지 포함한 v0.6 1차 재인수 결과
- [reports/2026-08-24-opening-hours-first-pass.md](./reports/2026-08-24-opening-hours-first-pass.md): 출입구를 제외한 동부 PoC 운영시간 7곳 raw-first 발행·날짜별 coverage 결과
- [reports/2026-08-24-opening-hours-generation-integration.md](./reports/2026-08-24-opening-hours-generation-integration.md): 운영시간 이중 후보·버스-only 실제 TMAP Generate→Evaluate와 잔여 coverage 점검
- [reports/2026-08-24-r-hotel-opening-hours-expansion.md](./reports/2026-08-24-r-hotel-opening-hours-expansion.md): 제주알호텔 실제 버스 후보 5곳의 TourAPI raw-first 운영시간 확대와 4/5 staged 판정
- [reports/2026-08-24-r-hotel-hours-activation-and-live-reacceptance.md](./reports/2026-08-24-r-hotel-hours-activation-and-live-reacceptance.md): R호텔 운영시간 1.0 활성화와 세 전략 100% 라이브 Generate→Evaluate 재인수
- [reports/2026-08-24-global-hours-and-exact-timetable-pass.md](./reports/2026-08-24-global-hours-and-exact-timetable-pass.md): 전역 운영시간 재개 수집 836/1,019와 exact 버스 369/974의 안전한 후속 상태
- [reports/2026-08-25-global-hours-complete-snapshot.md](./reports/2026-08-25-global-hours-complete-snapshot.md): 전역 상세소개 1,019건 수집 완료, 주간 휴무 근거 모델링과 활성화 전 잔여 공백
- [reports/2026-08-25-ambiguous-hours-activation.md](./reports/2026-08-25-ambiguous-hours-activation.md): 전역 조회 완전성과 정확 운영시간 품질을 분리한 v12 활성화 및 모호한 장소의 주의 추천 검증
- [reports/2026-08-24-strategy-slack-and-revalidation-reacceptance.md](./reports/2026-08-24-strategy-slack-and-revalidation-reacceptance.md): 전략별 이동 여유 위험도 일치와 활동 지연·회복안 라이브 재판정
- [reports/2026-08-30-residual-primary-gap-closure.md](./reports/2026-08-30-residual-primary-gap-closure.md): exact 버스·휴게시간·입구·접근성·식이·외부 종단의 최종 1차 안전 경계와 잔여 데이터 조건
- [reports/2026-08-31-codex-mcp-setup-and-live-acceptance.md](./reports/2026-08-31-codex-mcp-setup-and-live-acceptance.md): Codex 보안 실행점·stdio·품질 게이트와 당일 라이브 인수 공백

## 현재 기준

- PDF는 아이디어 참고자료이며 이 문서가 제품 구현의 우선 기준이다.
- 응답은 성공 시 정확히 3개의 추천 일정을 포함한다.
- 세 일정은 모두 사용자의 필수 조건을 만족해야 하며 제목만 다른 중복 경로여서는 안 된다.
- AI는 장소 탐색, 도구 선택, 후보 생성과 설명을 담당한다.
- 시간 합산, 영업시간, 버스 탑승 가능성, 휴식시간, JSON 스키마는 코드가 검증한다.
- 확인되지 않은 시간을 사실처럼 생성하지 않는다. 확인되지 않은 값은 `null`과 상태값으로 표현한다.
- 현재 핵심 제품은 미래 날짜의 사전계획이다. 실시간 도착정보는 계획 생성의 필수 근거가 아니라 출발 전 선택적 재검증 자료다.
- 버스 일정은 장소 출입구에서 승차 정류장까지의 도보와 하차 정류장에서 다음 장소 출입구까지의 도보를 반드시 포함한다.
- 현재 추천 범위는 `JEJU_ALL/ALL`이며 이전 선택 일정 최대 4일을 포함해 5일차까지 처리한다.
- 운영시간이나 입구가 없더라도 계산 가능한 시간 상태는 유지하고 근거 상태만 분리해 낮춘다.

## MCP 공개 도구

```text
recommend_jeju_day_trips
evaluate_jeju_day_trip
revalidate_jeju_day_trip
search_jeju_places
inspect_jeju_bus_stop
preview_jeju_transfer
```
