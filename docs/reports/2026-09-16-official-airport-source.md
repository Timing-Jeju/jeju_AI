# 공식 제주공항 source 연결

관련: AI #22, BE #271. FE UI와 기존 공개 Pydantic v0.7 도구 schema는 변경하지 않는다.

## 출처와 의미

- 한국공항공사 공항 위치정보: https://www.data.go.kr/data/15002851/fileData.do
- 2026-09-16 확인한 페이지의 이용허락범위는 제한 없음이다. 내부 원본 보존·정규화에만
  사용하며 공개 재배포는 승인하지 않는다.
- 원본 표기 날짜: 2025-08-01, CP949 CSV 885 bytes, 전체 14개 공항.
- 원본 SHA-256: `2d16655b866cda93790d44cc8460f42eb7ba7c2dde3be72adfef2cf8719a39a6`.
- 원본 제주 행: 이름 `제주`, 주소 `제주 제주시 공항로 2`, WGS84 `33.511111,126.492778`.
- 운영상 명시적 이름/ID 매핑: `제주` → `제주국제공항`, `kac.airport:CJU`.
  CJU는 원본의 content ID가 아니다. TourAPI ID를 생성하거나 매장 ID를 공항으로 바꾸지 않는다.
- 이 좌표는 **공항 대표점**이며 터미널 출입구·보행 접근 가능성을 검증한 좌표가 아니다.
  일반 요청에만 `REPRESENTATIVE_PLACE_POINT`를 사용하고 접근성 필수 요청은 기존대로 차단한다.
- 라이선스 fingerprint는 URL, 데이터명, 허락범위, 검토일을 `|`로 연결한 UTF-8 문자열의
  SHA-256이다. 원본 파일 checksum과 구분한다.

## 수집과 수명

`kac.airport`는 승인된 HTTPS 파일 경로로만 내려받는다. FILE도 host/path/redirect 제한을
그대로 적용한다. 원본 전체를 private raw storage에서 checksum 검증한 뒤 제주 한 건을
정규화하여 append-only publication으로 만든다. importer의 `raw_row_count=1`은 이번
등록 대상인 제주 범위의 행 수이며 원본 파일 전체 행 수가 아니다.

대표점 원본 날짜와 조회일을 분리한다. 최초 조회 후 30일을 내부 재검토 기한으로 사용하며,
한국공항공사가 30일마다 갱신한다는 뜻이 아니다. 동일 원본 재실행은 관측시각을 갱신하지
않는다. 만료 뒤 동일 원본의 새 검토를 기록하는 갱신 작업은 별도이며 현재 importer가
자동으로 유효기간을 연장하지 않는다.

`airport_anchor_ready`는 공항 한 건과 활성 제주 polygon을 검증하는 내부 publication
capability다. 공항 한 건을 관광지 전체 `place_search_ready`로 가장하지 않는다.

## 실행

```sh
python scripts/import_kac_airport.py /private/airport.csv \
  --source-date 2025-08-01 --sha256 <verified-sha256>
python scripts/import_kac_airport.py /private/airport.csv \
  --source-date 2025-08-01 --sha256 <same-sha256> --activate
```

기존 importer 전용 환경을 사용한다. 키·DSN은 코드/로그에 출력하지 않는다.
STAGED 이후 같은 파일로 활성화를 재시도할 수 있다. 오래된 STAGED나 이미 게시된 파일을
새 관측처럼 활성화하지 않는다. 게시된 원본을 재실행하는 것은 유효기간 갱신 명령이 아니다.

## 확인 근거

- source 미등록, normalizer 미구현, import 메서드 미구현, 공항별 검색 유효기간 누락,
  관광 후보에서 공항 제외 누락, STAGED 재개 helper 미구현의 RED 확인 후 GREEN.
- 독립 사전 검토에서 단계적 활성화 오류를 발견해 수정.
- AWS 원본 STAGED → 같은 파일 재실행 → ACTIVE를 실제 확인.
  publication: `680fe670-bcfa-49cf-bcea-9b8070d5e817`.
- importer 계정으로 runtime 전용 view를 읽던 오류를 발견하고, 권한 확대 없이
  projection 및 active snapshot 조회로 수정하여 실제 활성화 통과.
- AWS 검증용 MCP health/ready, 무인증 401, 여섯 도구 계약 해시, 공항 검색,
  공항→실제 숙소의 walk/taxi 이동 미리보기 통과.
- 공식 공항→공항의 하루 요청에서 서로 다른 세 전략 생성 성공(약 2~3초).
- 2026-09-30 버스 전용 요청도 후보 세 개 생성, 각각 evaluate 검증,
  CONFIRMED 정류장 조회까지 통과하여 기존 월말 버스 연결 회귀를 확인.
- 전체 Python 테스트 통과(외부 live 테스트 9개 skip), Ruff, Pyright,
  한글 목적 docstring 검사 통과. 위 AWS 검증은 별도로 실제 수행했다.
- 실제 MCP 응답 envelope는 약 536KB였다. BE 기본 256KiB 수신 한도 때문에
  발생한 timeout을 합성 TLS 응답으로 재현하여 BE #271에서 MCP 전용 4MiB
  제한으로 수정했다. AI 실행 제한시간을 늘리거나 수신 제한을 제거하지 않았다.
- 경로 수치·geometry·응답 전체는 이 보고서에 저장하지 않는다.

## 운영 반영과 연동 확인

- AWS MCP image: `timing-jeju-mcp:airport22-20260916`,
  digest `sha256:1943a496592a826f2bc726b11337a7e668f042bb9dd86cee85870c7196d352d6`.
  기존 운영 image 위에 이번 변경만 적용했다. 기존 버스 코드/데이터는 보존했다.
- DB 사전 백업의 archive 목록 확인 후 공항 한 건을 게시했다.
  기존 MCP 컨테이너는 중지된 `jeju-mcp-before-airport22`로 보존했다.
- 운영 교체 후 health/ready, 인증 거부, 계약 해시, 공항 검색, 이동 미리보기,
  후보 세 개 생성을 다시 확인했다.
- 실제 FE TypeScript API wrapper와 Axios → 로컬 Spring BE/실제 PostGIS → AWS MCP로
  운영 교체 전/후 각각 새 여행을 생성했다. 두 번 모두 다음을 확인했다.
  - 생성 접수 202, queued → running → succeeded, success 후보 정확히 세 개.
  - 후보 일정 GET 200, 선택 적용 200, Trip의 활성 버전이 선택 후보와 일치.
  - 동일 멱등성 키와 기존 ETag로 재전송한 적용이 정상 replay 처리됨.
- FE UI/타입/API 코드와 BE/FE 배포는 변경하지 않았다. 인증은 로컬 테스트 fixture를
  주입했으므로 네이티브 화면 및 실제 Supabase 로그인 E2E 검증으로 확대 해석하지 않는다.
- BE 코드와 DB migration은 별도 BE #271 변경이다. AWS MCP 반영만으로 BE 운영에
  적용되는 것이 아니며, BE 배포 담당자가 공항 binding import와 함께 반영해야 한다.

## 운영상 제한

대표점 기반 경로 성공은 검증된 터미널 입구나 이동약자 접근성의 증거가 아니다.
공식 원본 재검토 기한은 조회 후 30일이며 동일 파일의 자동 수명 연장은 구현하지 않았다.
