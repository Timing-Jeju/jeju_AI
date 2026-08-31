# v0.6 버스-only 1회 환승 후보 탐색 인수 (2026-08-18)

## 결론

`Offline and DB-only Pass`다. 버스-only 장소 후보 모수와 전략 뼈대가 직통 운행편에만
의존하던 간극을 제거했다. 요청일 exact stop-time을 한 번 읽어 메모리 그래프로 만든 뒤,
같은 confirmed 정류장 또는 300m 이내 정류장에서 10분 안전시간과 사용자 대기한도를 만족하는
1회 환승을 후보로 포함한다. 실제 공개 추천은 기존과 같이 TMAP 보행 및 전체 타임라인 검증을
통과해야만 성공한다.

사용자의 당일 SK API 사용량 보호 요청에 따라 이번 검증에서는 TMAP·TAGO를 호출하지 않았다.

## 구현 범위

- 버스-only 공간 prefilter는 숙소와 장소 양쪽에 요청일 exact 운행 정류장이 있는지만 확인한다.
  같은 직통 trip을 미리 요구해 환승-only 장소를 누락하지 않는다.
- 후보 장소별 nearest confirmed 정류장은 각각 최대 12개로 유지한다.
- 요청일 calendar·exception·timetable 유효기간을 통과한 stop-time 약 1.7만 행을 한 번 읽고,
  trip과 stop sequence 순서로 메모리 그래프를 구성한다.
- 첫 노선과 두 번째 노선의 공개 노선번호가 같으면 환승으로 인정하지 않는다.
- 환승 정류장 직선거리 300m, 환승 안전시간 10분, 두 번째 버스 대기 입력 한도를 적용한다.
- 정류장 쌍별 시간대마다 빠른 후보만 bounded 보존해 하루 후반 운행편을 잃지 않으면서 조합
  폭증을 막는다.
- 후보 뼈대는 먼저 출발한 편이 아니라 문-to-문 도착이 빠른 편을 우선한다.
- 최종 라우터는 기존대로 TMAP 접근·환승·하차 도보, 최대 접근 20분, 정확한 두 bus ride를
  다시 검증한다. 후보 그래프만으로 공개 성공을 만들지 않는다.

## DB-only 검증

대표 요청은 제주알호텔, 2026-08-18 07:00~19:00, 버스-only, 구간당 최대 환승 1회다.

| 항목 | 결과 |
|---|---:|
| 동적 후보 장소 | 16 |
| exact 직통 후보 leg | 3,948 |
| exact 1회 환승 후보 leg | 19,401 |
| 장소·공간 prefilter | 3.319초 |
| 직통·환승 그래프 구성 | 3.659초 |
| 세 전략 primary/fallback 조립 | 7.097초 |
| balanced 후보 | 2개, 장소 5개씩 |
| experience_max 후보 | 2개, 장소 5개씩 |
| relaxed 후보 | 2개, 장소 4개씩 |
| TMAP/TAGO 호출 | 0 |

Postgres 읽기에는 60초 statement timeout을 적용했고 모든 단계가 제한 안에서 끝났다. 장소명,
노선 원문, 사용자 입력, geometry, 외부 provider raw body는 출력하거나 저장하지 않았다.

## 회귀 및 보호 조건

- 동일 공개 노선번호 환승 거부: Pass
- 두 번째 버스 대기 30분 초과 거부: Pass
- confirmed 정류장 300m 초과 환승 거부: Pass
- 모든 연속 leg가 1회 환승인 balanced 뼈대 완주: Pass
- 한글 테스트 목적 검사: Pass
- Ruff: Pass
- Pyright: 0 errors
- offline pytest: 394 passed, 9 skipped
- 전역 exact coverage 369/974를 1.0으로 승격하지 않음
- 외부 호출 40회·bus walk 20회 제한 변경 없음
- 서로 다른 최종 추천 세 개를 만들지 못하면 부분 결과 없이 전체 실패

## 남은 live 인수

API 사용량 초기화 뒤 이 후보 그래프로 Generate를 실행하고, 선택된 1회 환승 구간의 두
`bus_rides`와 세 보행 연결을 Evaluate에서 대조한 다음 현재 정류장에 한정한 same-day TAGO
Revalidate를 수행한다. 이 live 인수를 실행하기 전에는 이번 DB-only 결과를 외부 경로 성공으로
주장하지 않는다.
