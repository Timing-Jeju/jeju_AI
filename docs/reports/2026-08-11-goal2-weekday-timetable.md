# Goal 2: 2026-08-14 평일 시간표 publication

상태: `Pass`

## 완료

- 공식 XLSX raw ZIP에서 `WEEKDAY` calendar, 유일 TAGO route pattern, 전체 route-stop,
  주요 정류장 stop time을 typed `TimetableBundle`로 변환하는 builder를 추가했다.
- 목표일은 2026-08-14처럼 실제 평일이어야 하며, 토·일요일을 평일로 발행하지 않는다.
- 공식 평일 source reference, timetable 유효기간, BIS 공지 검토 URL과 검토 기준일이 없으면
  fail-closed 한다.
- raw ZIP은 private object storage 등록 후에만 정규화·publication된다.
- dry-run과 `--execute`가 같은 변환·참조 무결성 검사를 사용한다.
- staged TAGO route-stop 4,239행과 공식 XLSX 운행 180행을 다시 결합해 owner-only raw ZIP을
  생성했다. 같은 이름의 provider 정류장이 둘 이상인 checkpoint는 exact stop-time에서 제외했다.
- publication `2d49ecf2-8fe0-4ac4-a3a6-4ce70e3645b6`을 발행·활성화했다.
- active projection은 route-stop 4,239행, `WEEKDAY` calendar 1행, trip 180행,
  stop-time 1,321행이다.
- 2026-08-14 단일 날짜 `future_bus_planning_ready` coverage는 `1.0`이다.

## 실행 순서

1. 기존 staged TAGO route-stop publication에서 38개 pattern의 전체 정류장 순서를
   `mapping-manifest.json`에 내보낸다.
2. 공식 XLSX 180개 운행행의 유일 pattern mapping과 주요 정류장 provider stop ID를 같은
   manifest에 기록한다.
3. 공식 평일 출처와 2026-08-11까지의 공지 검토 근거를 `service-day-manifest.json`에 기록한다.
4. 두 XLSX와 세 manifest를 private raw ZIP으로 묶는다.
5. 아래 dry-run 후 `--execute`로 STAGED publication을 만든다.
6. `future_bus_planning_ready`를 2026-08-14 단일 날짜로 측정하고 coverage 1.0일 때만
   `--execute`로 active pointer를 전환한다.

```text
uv run jeju-data manual-import --dataset official-bus-timetable \
  --file PRIVATE_RAW_ZIP --source-date 2026-08-14
uv run jeju-data manual-import --dataset official-bus-timetable \
  --file PRIVATE_RAW_ZIP --source-date 2026-08-14 --execute
uv run jeju-data measure-coverage --publication PUBLICATION_UUID \
  --capability future_bus_planning_ready \
  --service-date-from 2026-08-14 --service-date-to 2026-08-14 --execute
```

## 검증 공백

- 이 publication은 2026-08-14 평일 하루만 승인한다. 2026-08-15 광복절 calendar의 근거가
  아니며 `BUS_SERVICE_DAY_UNVERIFIED`를 해소하지 않는다.
- XLSX checkpoint와 동일 이름의 provider 정류장이 둘 이상인 경우 해당 시각을 어느 정류장에도
  추정 배정하지 않았다.
- 버스 문-to-문 성공에는 별도로 verified 장소 입구와 access/egress TMAP 보행이 필요하다.
