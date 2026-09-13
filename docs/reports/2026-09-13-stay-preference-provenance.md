# 서버 체류시간 출처 계약 (#19)

## 상태

로컬 구현·검증 완료. 배포/활성화 완료는 아니다. BE #53/#79 연결의 선행 계약이다.

## RED → GREEN

- 기존 모델은 source 필드가 없고 서버 정책 필드를 extra_forbidden으로 거부했다.
  새 계약 테스트 7개 중 기존 호환·서버 입력 3개 실패를 확인했다.
- Pydantic PlaceDurationPreference에 기본 user_requested와 서버 place_override/category_default,
  정책 버전 및 timezone-aware 시행 시각을 추가했다. 사용자/정책 혼합과 서버 출처 누락은 거부한다.
- requested_stay_minutes는 사용자 또는 서버가 확정해 요청한 고정 체류 제약임을 명시했다.
  새 시간을 추정하지 않으며 서버 정책의 독립 외부 검증을 주장하지 않는다.
- 기존 세 전략 생성 테스트를 세 출처로 확장해 모두 같은 90분을 보존함을 검증했다.
- 최초 전체 검사에서 합성 산출물/체크섬 드리프트 2개 실패를 확인했다. 정규 생성 스크립트로
  schema, MCP manifest, synthetic artifact manifest, SHA manifest를 갱신한 뒤 통과했다.
- 한글 목적 검사, Ruff, Pyright 성공. pytest 595 passed, 9 skipped(기존 환경 의존),
  Starlette httpx deprecation warning 1건이다.

## 검증 공백과 배포 순서

- 독립 부분 리뷰에서 BE/AI의 체류시간 의미 충돌은 해소됐고 추가 차단 finding은 없었다.
  이는 전체 BE worker/후보 저장/조회/적용에 대한 승인이나 배포 증거가 아니다.
- BE는 사용자 지정값에는 기존 두 필드를 유지하고 서버 정책 값에만 출처 세 필드를 추가한다.
- 변경된 recommend/evaluate/revalidate 입력과 recommend 출력 hash를 BE manifest에 함께 반영한다.
  AI 새 schema 배포와 BE hash 동기화가 완료되기 전 활성화하지 않는다.
- 기존 사용자의 두 필드 입력은 호환되지만 tools/list fingerprint가 바뀌므로 서비스 간 배포 조율은 필수다.
- 원문·geometry 저장, 외부 데이터 소스 및 UI 변경은 없다.
