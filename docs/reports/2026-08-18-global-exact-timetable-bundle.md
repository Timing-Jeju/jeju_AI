# 제주 전역 exact 평일 시간표 bundle 준비 결과

2026-08-18 평일을 대상으로 owner-only 공식 BIS workbook 234개와 활성 TAGO route-stop을
결합했다. 원본 workbook과 생성 ZIP은 `tmp/private`의 ignore 대상이며 이 문서는 집계만
보존한다.

## 안전 경계

- snapshot ID는 `jeju-bis-2026-08-17-234`로 고정했다.
- 전체 checksum manifest의 canonical SHA-256은
  `b298bdb99b4597157a9a5d1bb2d3c116478ed10055988c6dea4fc3795ffb8481`로 pin했다.
- workbook 한 개라도 추가·누락·변경되면 raw loader가 거부한다.
- derived stop time은 원본 workbook의 동일 sheet·행에서 같은 순서로 확인돼야 한다.
- provider stop ID와 route sequence는 선택된 pattern의 route-stop 행과 정확히 일치해야 한다.
- 시행일이 없거나 목표일보다 늦은 행은 발행 후보에서 제외한다.
- 감사된 exact trip·노선번호·pattern 수가 달라지면 build가 실패한다.

## 생성 결과

| 항목 | 값 |
|---|---:|
| workbook | 234 |
| route-stop | 16,985 |
| scheduled trip | 2,586 |
| scheduled stop-time | 14,967 |
| exact 노선번호 | 186 |
| exact route pattern | 313 |
| owner-only ZIP 크기 | 2,104,198 bytes |
| owner-only ZIP SHA-256 | `5e34f781025e06abff85ad2e7423cf671a9bbb97287c2797ad0e7706716fc114` |

`build_weekday_timetable_bundle`과 `validate_timetable_bundle`을 모두 통과했다. 이어서
`jeju-data manual-import --dataset official-bus-timetable`을 `--execute` 없이 실행한 결과도
Pass였으며 object storage와 DB는 변경되지 않았다.

일반화 mapper만 사용한 최초 bundle은 201·211·212의 복수 pattern을 안전하게 제외했기 때문에
기존 동부 180 trip이 빠지는 회귀가 발견됐다. 해당 최초 bundle은 publication
`3480fe0f-7ab2-4289-8039-4e586dabeb1d`로 STAGED까지만 저장했고 coverage를 발행하지 않아
활성 snapshot `a128e806-1d15-4f86-83e2-303a78b4b1ad`를 변경하지 않았다. 수정 bundle은 같은
pinned workbook에서 기존 동부 exact mapping을 다시 생성해 180 trip·1,321 stop-time을
overlay한다. 최종 typed bundle과 manual-import dry-run은 다시 Pass했다.

## 운영 판정

이 bundle은 exact로 결합된 행만 포함하지만 공식 workbook과 TAGO 카탈로그의 모든 노선을
포괄하지 않는다. 코드 PR을 먼저 병합한 뒤 최신 `main`에서 raw-first import를 실행해 STAGED
publication으로 검증한다. 기존 활성 동부 publication은 새 publication의 coverage와 지역별
회귀가 확인되기 전까지 교체하지 않는다.
