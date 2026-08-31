# 제주 전역 공식 시간표 범위 감사

감사일은 2026-08-18이며, owner-only 원본은 저장소에서 제외된
`tmp/private/official/jeju-bus-timetable/2026-08-17/` 아래에서만 읽었다. 이 문서는 원본
workbook 내용을 재배포하지 않고 검증 결과의 집계만 기록한다.

## 재현 명령

```bash
set -a
source .env
set +a
.venv/bin/python scripts/audit_global_timetable_scope.py \
  --input-dir tmp/private/official/jeju-bus-timetable/2026-08-17/workbooks \
  --checksum-file tmp/private/official/jeju-bus-timetable/2026-08-17/workbooks.SHA256SUMS
```

범위가 완전히 일치하지 않으면 감사 명령은 JSON 결과를 출력한 뒤 종료 코드 2를 반환한다.
따라서 CI나 운영 절차가 부분 범위를 전역 성공으로 오인하지 않는다.

## 결과

| 항목 | 값 |
|---|---:|
| checksum 검증 workbook | 234 |
| workbook sheet | 440 |
| workbook 노선번호 | 255 |
| 활성 TAGO 노선번호 | 247 |
| 일치 노선번호 | 245 |
| 노선번호 후보 coverage | 99.190283% |
| 활성 TAGO route pattern | 974 |
| 일치 노선번호에 속한 route pattern | 972 |
| route pattern 후보 coverage | 99.794661% |

활성 TAGO에는 있으나 공식 workbook 집합에는 없는 노선은 `202-3`, `358-2`다. 공식
workbook에는 있으나 활성 TAGO 노선 카탈로그에는 없는 노선은 `369`, `590`, `900`, `910`,
`921`, `922`, `924`, `1100`, `1100-1`, `1950`이다.

## 판정

`scope_catalog_aligned=false`이며 다음 두 차단 사유를 유지한다.

- `TIMETABLE_ROUTE_NUMBER_MISSING_FROM_TAGO_CATALOG`
- `TIMETABLE_ROUTE_NUMBER_MISSING_FROM_WORKBOOKS`

99.79%는 노선번호가 같은 route pattern의 후보 비율이지 exact timetable 매핑률이 아니다.
동일 노선번호에 여러 route pattern이 있으므로 다음 단계에서는 각 운행행의 시간 checkpoint를
정류장 순서와 결합해 유일한 pattern만 통과시킨다. 모호하거나 정류장 시각이 두 개 미만인 행은
발행하지 않으며, 이 감사 결과만으로 `JEJU_ALL/ALL`의
`future_bus_planning_ready`를 활성화하지 않는다.
