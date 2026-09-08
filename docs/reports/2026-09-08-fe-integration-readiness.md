# FE 기준 생성·평가·일정 적용 통합 진행 기록

상태: **Partial — 통합 완료 아님**. 운영 배포·운영 DB 변경 없음.

## 검토 기준

사용자가 제공한 0→10 순차 계획을 기준으로 실제 화면, Spring 구현, MCP 계약을 다시 읽었다.
기존 문서의 `ready` 표기를 구현 증거로 사용하지 않았다.

- FE base: `Timing-Jeju/jeju_FE`의 `origin/main` (실제 SHA는 FE `docs/planner-integration.md` 참고).
- BE base: `Timing-Jeju/jeju_BE` develop `e677725`.
  `/Users/gwongwangjae/jeju_BE_planner_audit`의 detached 읽기용 worktree로 검사했다.
  기존 `test/202-mcp-live-acceptance` 작업 checkout은 변경하지 않았다.
- AI base: `Timing-Jeju/jeju_AI` develop `2d9a6fbedb97515d6284b96757091f5cc428cfb0`.
  `jeju_algo`는 개발·검증 공간이며 팀 대상은 `timing-jeju/develop`이다.
- 공개 MCP 버전은 여전히 `0.7.0`이다. facts와 위치 비수집 전환을 구현하지 않은 채
  버전 문자열만 `0.8.0`으로 올리지 않았다.

## 화면 → REST → MCP → 저장 매핑 (작업 0)

REST는 `/api/v1` 기준이다. 상태는 이 작업 종료 시점의 코드 기준이며 live 검증을 뜻하지 않는다.

| 화면 / 사용자 동작 | REST / 인증 | MCP | 저장 결과 / 현재 상태 |
| --- | --- | --- | --- |
| login 이메일·비밀번호 | Supabase Auth `signInWithPassword` | 없음 | **FE 구현**, 세션 복원·갱신·종료 연결. 실제 계정 검증 미실행 |
| 앱 재시작·복귀 | Supabase `getSession` + `getUser`, auth event | 없음 | 인증 세션만 복원. 여행 복원은 미구현 |
| mypage 로그아웃 | Supabase Auth local scope signOut | 없음 | 성공 후 사용자·여행·찜·검토 메모리 초기화 |
| trip-conditions 저장 | POST `/trips`, PUT preferences/place-preferences/transport-event, POST accommodations | 없음 | BE 구현 존재, **FE 미연결**. 로컬 임시 보관과 서버 저장 상태 분리 |
| 장소 검색·선택 | GET `/places`, `/places/{placeId}` | 없음 | BE 구현 존재, **FE canonical ID 미연결**. 현재 이름 기반 항목은 이관 대상 아님 |
| 찜 목록·변경 | `/saved-places` | 없음 | BE 구현 존재, FE 로컬 편집. 초기 mock 찜 목록 제거 |
| calendar Day 생성 | POST `/trips/{tripId}/generation-runs` | `recommend_jeju_day_trips` | **BE 미구현**. FE 생성 차단, 임의 공항 anchor와 모의 수치 계산 제거 |
| schedule-loading 진행 | GET `/trips/{tripId}/generation-runs/{runId}` | 직접 호출 없음 | **BE 미구현**. FE 준비 중 표시, 자동 타이머·진행률 제거 |
| schedule-review 후보 3개 비교 | 생성 run 결과 GET | 생성 후 후보별 `evaluate_jeju_day_trip` | **미구현**. 저장 허용 정책 및 생성 계약에 막힘 |
| 후보 선택·확정 | POST `/trips/{tripId}/generation-runs/{runId}/candidates/{candidateId}/apply` | 없음 | **미구현**. 로컬 confirm 성공 제거, 화면 비활성 |
| 앱 재시작 후 active 일정 | GET `/trips/{tripId}/schedule` | 없음 | BE ScheduleController 구현 존재, FE adapter·복원 미구현 |
| 가능성 재검사 | POST `/trips/{tripId}/feasibility-runs` 및 결과 GET | `evaluate_jeju_day_trip` | **BE 미구현**. 모의 재계산 제거 |
| schedule-leg 상세 | GET `/trips/{tripId}/schedule-versions/{versionId}/legs/{legId}` | 없음 | **BE 미구현**. immutable lineage·저장 projection 확정 필요 |
| 일정 수동 편집 | BE ScheduleMutationController의 mutation | 변경 후 평가 요청 | BE 구현 존재, FE 미연결. 적용 전 후보 편집은 차단 |
| 빈시간 채우기·AI 수정·대체 경로 | 후속 revision / gap 계약 | 후속 도구/흐름 | **범위 밖·미지원**. 모의 추천/대체 경로 수치 제거 |
| live-map·recovery | 후속 live/recovery API | `revalidate_jeju_day_trip` | **범위 밖·비활성**. 현재 FE에서 live content를 mount하지 않음 |
| FCM | 후속 push API | 없음 | **범위 밖** |
| signup·find-account | 기존 모의 화면 | 없음 | **범위 밖·미연결**, 실제 계정 생성/복구 완료 근거가 아님 |
| withdraw | 향후 실제 탈퇴 API | 없음 | 로그아웃을 탈퇴로 가장하던 동작 제거, 미지원 안내 |

## 확인된 선행 계약 충돌 (작업 1)

아래는 이 작업자가 승인한 정책이 아니다. 실제 승인 또는 별도 계약 변경이 필요하다.

1. BE #220은 OPEN인 **사용자 현재·간접 위치 무수집 계약**이다.
   #216은 OPEN인 **Leg 상세 영속 projection·TMAP 비영속 정렬 계약**이다.
   번호와 의미를 혼동하지 않는다. #221/#222/#225/#223/#224도 모두 OPEN이다.
2. AI `config/data_sources.toml`의 `tmap.pedestrian`, `tmap.driving`은
   `MEMORY_ONLY_LT_24H`, `raw_private_storage_allowed=false`,
   `internal_derivative_allowed=false`다. `tago.bus-arrival`도 비영속이다.
   정규화나 파생이라는 이유로 후보·일정·leg 수치를 DB/Redis/파일에 저장할 수 없다.
   승인 근거를 사용자에게 요청했으며, 근거 확인 전 저장·적용은 차단한다.
3. BE ADR-0052는 TourAPI/TAGO/TMAP 공급을 private MCP가 소유하도록 결정했다.
   이번 계획의 Spring facts 공급과 다르므로 successor ADR 및 source owner 정렬이 필요하다.
4. BE `planner-fe-integration/contract.json`은 `SelectedCandidate.rank=1`,
   `strategy=balanced`만 허용하고 cost를 non-null로 요구한다.
   3개 비교·명시 선택, 요금 null과 unknown 상태를 요구하는 이번 계획과 호환되지 않는다.
5. BE #89(생성·revision 계약)는 OPEN이다. 생성·평가 Controller와 private facts API가 없다.
   client 함수나 OpenAPI 타입을 임의로 만들어 연결 완료로 표시할 수 없다.
6. 기존 숙소 계약은 `[checkInDate,checkOutDate)` 및 `checkInDate < checkOutDate`다.
   당일 여행의 계획 숙소 anchor와 날짜별 마지막 날 anchor를 손실 없이 전달할 계약이 필요하다.
   BE 여행 CRUD의 30일과 planner의 최대 5일도 생성 입력에서 별도로 제한해야 한다.

## 이번 구현과 안전 경계

- FE `feat/planner-auth-foundation`에서 인증 및 차단 동작을 구현했다.
- Supabase publishable key만 허용한다. 세션과 비밀번호를 Zustand에 복제하지 않는다.
- API client는 설정된 origin의 `/api/v1/` 상대 경로만 허용하고 baseURL override를 거부한다.
  사용자 access token만 첨부하며 자동 mutation 재시도는 없다.
  Axios의 raw config/body/token/cause를 외부 오류 객체로 전달하지 않는다.
- 인증 상태 변경·사용자 전환 시 로컬 여행/일정/찜 상태를 초기화한다.
- 앱 종료 후 여행 복원, ETag, idempotent trip save, canonical selection, 실제 polling,
  후보 3개 비교와 atomic apply는 **아직 구현되지 않았다**.
- `PLANNER_AVAILABLE=false`는 실험용 임의 env toggle이 아니다.
  호환 계약·저장 정책·실제 staging 검증 전 활성화해서는 안 된다.
- 기존 Expo 화면 내용은 보존하고 검토·상세·live의 진입을 미지원 화면으로 막았다.
  `utils/schedule.ts`의 이름 해시/기본 이동수치/자동 체류시간 덮어쓰기는 제거했다.
- AI 수집기·DB 권한·공휴일·SGIS를 제거하지 않았다. AI #14 완료 조건을 만족하지 않는다.
- BE 소스/마이그레이션/리뷰 승인 상태 파일/외부 Notion·Figma를 변경하지 않았다.

## 검증 및 미실행 기준

FE Jest RED → GREEN은 모의 계산·로컬 확정 거부, 인증·API 모듈 부재를 먼저 확인했다.
컴포넌트 테스트는 density-only `@3x` 이미지도 실제 파일로 resolve한다.
FE Jest 26개와 typecheck/lint, Android·iOS Metro/Hermes export가 통과했다.
AI는 한글 테스트 설명 → Ruff → Pyright → pytest 순서로 통과했다
(561 passed, 9 skipped; live/provider 검증과 구분).
테스트 및 FE 검사 상세는 FE 문서에 기록한다.

- 실제 Supabase 로그인/BE HTTP: **SKIPPED**, staging endpoint·공개 키·합성 계정 미지정.
- native Maestro: **SKIPPED**, 이 환경에 `maestro`, `adb`, `xcrun simctl` 없음.
- 실제 승인 provider: **SKIPPED**, 자격증명·호출 예산·staging 계약 미준비.
- DB16/17·fresh/upgrade·동시성·Docker: 이 변경에서 BE/DB 구현 없음, **미실행**.
- Notion/Figma: 실제 page/node readback 없음, **not-linked**.
- 독립 Reviewer/PR: 미수행. BE AGENTS의 reviewer 승인이나 create-pr 절차를 대체하지 않는다.

## 다음 실행 경계

저장 허용 projection 및 source별 보존 근거를 확정하고 #220/#216/#89와 ADR을 정렬한다.
이후 계획된 위치 정리 순서와 Spring immutable projection/facts API를 진행한다.
Pydantic facts 및 MCP 0.8 → BE wire/hash/evidence guard → worker atomic completion →
생성/평가/적용 REST → 생성된 FE OpenAPI 타입·canonical ID·화면 연결 → 실제 staging 순서다.
후속 PR은 선행 반영 후 새 develop/main base에서 만들고 대응 PR·계약 checksum·SHA를 기록한다.

전체 목표의 6개 완료 조건 중 어느 것도 이 문서의 부분 구현만으로 충족됐다고 표시하지 않는다.

## 후속 작업: 저장 정책 근거 재확인 (2026-09-08)

공식 [TMAP API 약관](https://tmapapi.tmapmobility.com/terms.html)의
준수사항/제약사항을 다시 조회했다. 해당 페이지는 API로 얻은 데이터를 저장한 뒤
24시간 이상 사용할 수 없다는 제한을 명시한다. 이 문장은 개별 결과를 최종 일정에
영구 보관할 수 있다는 승인을 제공하지 않는다. 적용 상품·개별 계약의 예외도
이 조회만으로 확인되지 않았다.

| 필드/산출물 | 현재 프로젝트 정책 | 추가로 필요한 근거 |
|---|---|---|
| TMAP raw·geometry·polyline | 영속화 금지 | 이번 계획에서 저장 대상으로 전환하지 않음 |
| 개별 route duration/distance/fare | 메모리만 허용 | 필드별 저장·사용 기간과 적용 상품의 명시 근거 |
| route 수치로 파생한 도착시각·위험·일정 합계 | 저장 승인 미확인 | 파생 결과 및 최종 일정 보관 범위 |
| 사용자가 직접 선택한 canonical 장소·숙소·터미널 참조 | 사용자 계획 데이터 | provider 결과와 분리한 provenance 및 version lineage |
| source/run/snapshot 식별자 | allowlist 계약 필요 | 식별자만으로 원문·위치·비밀이 재유입되지 않는 검증 |

`config/data_sources.toml`의 `tmap.pedestrian`·`tmap.driving`은 여전히
`MEMORY_ONLY_LT_24H`, `raw_private_storage_allowed=false`,
`internal_derivative_allowed=false`다. 약관 페이지를 읽었다는 이유로 이 정책을
완화하지 않았다. [BE #216](https://github.com/Timing-Jeju/jeju_BE/issues/216)의
field allowlist와 owner 합의·Notion/Figma readback은 아직 미완료다.
후보 저장·적용 및 영구 복원 기능을 출시 가능으로 표시하지 않는다.

BE에서는 #220의 미래 계약을 현재 Swagger에 투영하는 선행 오류 #226을 별도
브랜치에서 수정 중이다. 이는 위 FE/AI 초기 변경의 검증 결과와 별도이며,
#220 자체의 공개 계약 전환·위치 제거 완료를 뜻하지 않는다.
