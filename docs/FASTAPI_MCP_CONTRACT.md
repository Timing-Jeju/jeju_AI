# FastAPI MCP private 연동 계약 v0.7.0

## 공개 도구와 단일 계약 원본

`src/jeju_trip/domain/models.py`의 Pydantic 모델만 공개 JSON 계약을 정의한다. stdio와 private
Streamable HTTP `/mcp`는 다음 여섯 도구를 동일 schema로 노출한다.

- `recommend_jeju_day_trips`
- `evaluate_jeju_day_trip`
- `revalidate_jeju_day_trip`
- `search_jeju_places`
- `inspect_jeju_bus_stop`
- `preview_jeju_transfer`

실제 checksum은 `docs/manifests/mcp-tools-v0.7.json`에 생성한다. 호출자는 시작 시
`initialize`와 `tools/list`를 수행하고 이름·input/output checksum이 다르면 fail-closed한다.

모든 `tools/call` arguments는 top-level `requestId`, `inputHash`, `request`를 필수로 가진다.
`requestId`는 1~128자의 제한된 ASCII 식별자이고 `inputHash`는 lowercase SHA-256 64자리다.
`inputHash`는 자기 자신을 제외한 실제 raw arguments, 즉 `requestId`와 `request`를 UTF-8,
object key 정렬, 공백 없는 JSON으로 canonicalize해 계산한다. FastAPI는 Pydantic 변환이나
도구 실행 전에 같은 규칙으로 재계산하며 불일치는 `MCP_INPUT_HASH_MISMATCH`로 전체 거부한다.

## Transport와 인증

- HTTP runtime: stateless Streamable HTTP, JSON response, path `/mcp`
- ingress: private network only, public port publication 금지
- TLS: certificate와 private key file 필수
- bearer: RS256만 허용, local JWKS `kid` 선택
- claim: 정확한 issuer·audience, `sub`, `iat`, `exp`, 1~128자 `jti`,
  `jeju:mcp:invoke` scope 필수
- token lifetime: `exp - iat <= 300초`
- health: `/health`; readiness: `/ready`

launcher는 `JEJU_RUNTIME_DSN`, TMAP/TAGO key, bind host/port, auth issuer/audience/JWKS file,
TLS certificate/key 경로와 최소 시스템 환경만 남긴다. proxy credential, Python injection,
Codex 내부 환경, importer/migrator DSN, 다른 `JEJU_*`는 제거한다.

## v0.7 FE 경계

`activity_window`가 절대 시각 경계이고 `day_boundary.start_place/end_place`가 장소 경계다.
경계를 생략한 날짜만 accommodation을 양쪽 endpoint로 사용한다. 추천은
`day_start_at`, `day_end_at`, `start_place_id`, `end_place_id`를 필수로 반환한다.
`place_duration_preferences`는 사용자가 지정한 체류시간이며 planner가 축소하지 않는다.

## Data와 lifecycle

외부 호출은 `config/data_sources.toml`의 승인 source만 사용한다. 사용자 JWT, 원문, GPS,
provider raw body와 TMAP geometry를 MCP payload 로그나 영속 저장소에 남기지 않는다. TMAP/TAGO
client는 정상 종료, tool 예외, 초기화 실패에서 모두 닫는다. 일정 성공은 서로 다른 세 전략이
모두 유효할 때만 허용하고 그 외에는 부분 결과 없이 `insufficient_feasible_routes`다.
