# 시작·종점 확대 시간표 증분 발행 결과 (2026-08-18)

## 판정

`Partial Pass`다. 시작·종점으로 추가 확정한 185개 trip을 기존 active 시간표에 증분 병합해
기존 evidence ID를 모두 보존한 새 publication을 활성화했다. 제주 전역 route coverage는
361/974로 미달이므로 global `future_bus_planning_ready`는 계속 비활성이다.

## 활성 publication

| source | publication | dataset |
|---|---|---|
| `jeju.bus-timetable` | `76f338a7-42ed-443c-861c-81ee402ba2a6` | `2026-08-18-c5ea2b4bfbd8` |

| projection | active rows |
|---|---:|
| route-stop | 18,438 |
| scheduled trip | 2,771 |
| scheduled stop-time | 15,945 |
| route pattern | 361 |

- base raw SHA-256:
  `5e34f781025e06abff85ad2e7423cf671a9bbb97287c2797ad0e7706716fc114`
- incremental raw SHA-256:
  `481bb2f23aacfdfb1ba064035e6f06131078fe78caac3aaf02e61afe144a7e92`
- incremental raw bytes: 2,134,208
- 두 raw 모두 같은 pinned workbook 234개 checksum manifest를 사용한다.

## evidence ID 회귀 발견과 복구

최초 확대 bundle은 모든 exact mapping을 다시 순번화했다. projection 수는 맞았지만 직전 active와
비교하면 기존 trip 2,226건과 stop-time 13,646건의 ID가 달랐다. append-only 원본은 남아
있었으나 기존 recommendation evidence 연속성을 깨뜨릴 수 있어 publication
`ae635f00-e7a8-4b0f-9627-3c1e6db85a4f`을 활성 상태로 유지하지 않았다.

1. 직전 정상 publication `ada744d5-2a8a-4e51-8fc4-f75d065ef7b9`로 active pointer를 rollback했다.
2. base raw checksum을 명시적으로 pin하고 typed bundle 전체를 다시 검증했다.
3. 동일 workbook·sheet·source row·route number의 기존 trip은 내용이 완전히 같을 때만 기존
   trip ID를 보존했다.
4. 새 source row 185건만 lineage SHA-256 기반의 결정적 `weekday-expansion-*` ID를 부여했다.
5. 기존 route-stop 또는 trip 내용이 달라지거나 기존 lineage가 사라지면 증분 build를 거부한다.

교정 publication의 양방향 비교 결과는 다음과 같다.

| 회귀 검사 | 결과 |
|---|---:|
| 기존 trip 누락 | 0 |
| 기존 stop-time 누락 | 0 |
| 신규 trip | 185 |

최초 publication과 rollback은 감사 가능한 activation/acquisition 이력으로 그대로 보존했다.

## coverage와 라이브 결과

- `JEJU_EAST/POC_V1 future_bus_planning_ready`: 1.0, 교정 publication 활성화 근거
- `JEJU_ALL/ALL future_bus_planning_ready`: 361/974 = 0.370637, 미발행·비활성
- scoped coverage SQL은 route-stop 전체 조인을 membership `EXISTS`로 바꿔 장시간 중간 결과를
  제거했다. 교정 staged publication 측정은 수 초 안에 완료됐다.
- 표준 버스-only 요청은 `DATA_NOT_READY`, missing은 `future_bus_planning_ready` 하나다.
- 동부 readiness를 명시한 라이브 버스-only 요청도 25.3초에 추천 0건과
  `ROUTE_EVIDENCE_MISSING`을 반환했다. 택시 구간은 0건이다.

따라서 데이터 범위가 늘었지만 서로 다른 세 개의 완주 일정을 만들 수 없다는 공개 불변조건을
완화하지 않았다.

## 데이터 보호

- base·확대·교정 ZIP은 Git ignore된 `tmp/private`에 mode `0600`으로 보관한다.
- workbook 원문과 셀 값을 저장소·보고서·로그에 출력하지 않았다.
- TMAP raw body·geometry·사용자 원문은 저장하지 않았다.
