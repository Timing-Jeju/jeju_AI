# 데이터와 저장 규칙

## Source contract

네트워크 전에 source ID, 라이선스 상태, host/path allowlist, 응답 크기, 보존 허용 여부를 검증한다. `PENDING` 또는 미등록 source는 호출하지 않는다. 수집 키는 source별 allowlist로만 importer에 전달한다.

## Raw-first publication

허용된 원본은 owner-only 임시 파일에서 SHA-256을 계산하고 `raw/v1/{source_id}/{prefix}/{checksum}.{ext}`로 private MinIO/S3에 조건부 업로드한다. HEAD로 길이·checksum·version을 재확인하기 전에 DB acquisition을 만들지 않는다. 정규화는 권한 0600 NDJSON spool을 거쳐 `VALIDATED` publication만 한 transaction에서 projection과 active snapshot으로 전환한다. raw object, publication, projection은 append-only다.

## 권한과 조회

`jeju_migrator`는 DDL, `jeju_importer`는 생명주기 INSERT, `jeju_runtime`은
`travel_read` view SELECT만 허용한다. 런타임에는 importer DSN을 전달하지 않는다.
실시간 TAGO 키는 source contract allowlist를 통과한 on-demand adapter에만 전달하며
응답 원문과 사용자 위치는 저장하지 않는다.

## TMAP

TMAP raw body·geometry·정확한 사용자 원문은 Postgres, MinIO, Redis, 파일, 로그에 저장하지 않는다. 프로세스 메모리 캐시만 사용하고 TTL은 23시간 50분 미만이다. 로그에는 fingerprint, 시각, latency, 상태, 안전한 reason code만 남긴다.

## 정류장과 입구

provider ID를 canonical ID로 덮어쓰지 않는다. 성공 버스 leg는 `CONFIRMED` mapping만 쓴다.
이름만 같은 정류장은 확인 근거가 아니다. 검증 입구가 없는 일반 요청은 active TourAPI 장소
대표좌표를 `PROVISIONAL_PLACE_POINT`로 명시해 사용할 수 있으며, 검증 입구로 조용히 가장하지
않는다. 이동보조·계단회피 필수 요청은 계속 `VERIFIED` 입구를 요구한다.
