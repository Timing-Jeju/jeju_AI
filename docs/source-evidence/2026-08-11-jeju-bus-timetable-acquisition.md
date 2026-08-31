# 제주 공식 버스 시간표 1차 수집 근거

## 승인 판정

- 공식 상세: https://www.data.go.kr/data/3043887/fileData.do
- 제공기관: 제주특별자치도
- 포털 표시: 무료, 이용허락범위 제한 없음
- 원본 화면: https://bus.jeju.go.kr/publicTrafficInformation/generalBusSchedule?viewtype=2
- 변경 공지: https://bus.jeju.go.kr/notice/list
- 프로젝트 범위: private 원본 보관·내부 정규화 허용, 공개 재배포 금지

## 2026-08-11 PoC 원본

| BIS group schedule ID | 노선 | 파일 SHA-256 | 확인 결과 |
|---|---|---|---|
| `405009` | 201 | `d3a6b9db15f0786a1ac1ab7865a7762f51b7521ef69e9844b1ca6e19b0e2b04e` | 2개 방향 시트, 시행일 2024-08-01 |
| `405011` | 211·212 | `2e0e13c4d980b7edaaa7540e2ffc013bdb3d43e9e19c7a46caccc66584c615eb` | 4개 노선·방향 시트, 시행일 2026-02-12 |

원본은 저장소의 ignore 대상인 `tmp/private/official/jeju-bus-timetable/2026-08-11/`에
소유자 전용 권한으로 보관했다. application publication에는 아직 넣지 않았다.

## 확인된 데이터 한계

공식 Excel은 출발편별 주요 정류장 시각과 선택 경유 여부를 제공하지만 모든 정류장의
stop time을 제공하지 않는다. TAGO active route에는 201번 운행 패턴이 20개 존재하지만 현재
active `bus_route_stop`은 0건이다. 따라서 이름만으로 정류장·방향·운행 패턴을 추정해
`CONFIRMED`로 승격할 수 없다.

다음 publication 조건은 아래와 같다.

1. TAGO `getRouteAcctoThrghSttnList` 계열 응답으로 각 provider route ID의 정류장 순서를 수집한다.
2. BIS 주요 정류장 열을 TAGO 정류장 ID와 방향·순서까지 대조한다.
3. 각 Excel 행을 실제 운행 패턴 route ID에 연결하고, 연결 불가 행은 거부한다.
4. 시행일 이후 공지를 병합해 유효기간과 예외 운행을 만든다.
5. 7개 정규화 CSV 묶음의 dry-run과 참조 무결성 검증을 통과한 뒤에만 발행한다.

이 조건 전에는 `jeju.bus-timetable` 계약은 승인 상태여도
`future_bus_planning_ready=false`를 유지한다.
