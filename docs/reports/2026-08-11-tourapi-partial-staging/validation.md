# 부분 데이터 기반 1차 시험 검증 결과

검증일: 2026-08-11 (Asia/Seoul)

## 최종 판정

- 부분 TourAPI 상세정보는 비운영 분석과 API 연결 PoC에 사용할 수 있다.
- 불완전 스냅샷과 장소 중심좌표는 운영 일정 생성의 evidence로 승격하지 않았다.
- 현재 운영 추천 요청은 정확히 추천 0개와 `DATA_NOT_READY`를 반환한다.
- 세 개의 검증된 추천이 없을 때 부분 성공하지 않는 계약은 유지된다.

## 확보된 시험 결과

| 항목 | 결과 | 운영 사용 |
|---|---:|---:|
| TourAPI 상세 조회 대상 | 1,019건 | 불가 |
| 완료 조회 | 939건 (92.15%) | 불가 |
| 누락 조회 | 80건 | 재수집 필요 |
| 운영시간 텍스트 보유 | 847건 | 분석 전용 |
| 안전하게 구조화된 장소 | 21곳 | 부분 snapshot이므로 불가 |
| 생성된 요일별 규칙 | 144건 | publication하지 않음 |
| TMAP 제주 대표 연결 | 3/3 통과 | 연결 PoC 전용 |

TMAP 대표 구간은 함덕→성산→중문→협재이다. 검증된 출입구가 없어 TourAPI 중심점을
임시로 사용했으며, TMAP 거리·시간·원본·geometry는 결과 파일이나 DB에 저장하지 않았다.

## 발견하고 수정한 결함

- `화요일`의 끝 글자를 일요일 하나로 오인하던 정규식을 수정했다.
- `화요일~금요일`, `토요일~월요일`, `평일`, `주말`을 정확한 요일 집합으로 구조화했다.
- `1~2월` 같은 계절 범위를 월요일 운영으로 오인하지 않고 `PARTIAL`로 남겼다.
- 위 세 시나리오는 First RED 테스트로 실패를 확인한 뒤 수정했으며 관련 테스트 22개가 통과했다.

## 운영 격리 확인

현재 active projection은 다음과 같다.

| projection/capability | 현재 상태 |
|---|---:|
| 장소 | 1,019건 |
| 운영시간 규칙 | 0건 |
| 검증된 장소 출입구 | 0건 |
| canonical 정류장 mapping | 0건 |
| `place_search_ready` | `true` |
| 그 외 생성 필수 capability | `false` |

실제 추천 호출 결과:

```text
status=insufficient_feasible_routes
recommendations=0
failure_code=DATA_NOT_READY
missing_capabilities=driving_routing_ready,future_bus_planning_ready,
opening_hours_ready,service_area_ready,walking_routing_ready
```

## 품질 게이트

- 한글 테스트 목적 검사: 통과
- Ruff: 통과
- Pyright: 오류 0건, 경고 0건
- 전체 offline pytest: 200 passed, 9 skipped
- 격리 PostGIS/MinIO live integration: 15 passed
- 실제 TMAP 제주 대표 구간 PoC: 3 passed

## 다음 데이터 적재 순서

1. TourAPI 상세정보 누락 80건을 재수집해 완전한 snapshot을 만든다.
2. 복합 운영시간 중 `PARTIAL`·`UNKNOWN`을 적용기간과 예외 휴무 구조로 보강한다.
3. 제주 경계 publication으로 `service_area_ready`를 검증한다.
4. 숙소·필수 관광지의 보행/차량 출입구를 검증해 발행한다.
5. 제주 버스 공식 시간표, 서비스달력, 변경공지와 canonical stop mapping을 완성한다.
6. 완성된 publication으로 coverage를 다시 측정한 뒤에만 capability를 활성화한다.
7. 활성 capability를 기반으로 세 전략 일정 생성과 전체 타임라인 검증을 실행한다.

화장실 데이터는 현재 제품 입력의 필수 조건이 아니므로 이 적재 순서와 readiness gate에서 제외했다.
