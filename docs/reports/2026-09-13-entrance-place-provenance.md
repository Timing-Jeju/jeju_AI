# 검증 입구와 canonical 장소의 근거 연결 (#21)

## 상태

AI source fact 보완 완료. BE 이동 구간 검증·후보 저장·적용 연결과 배포 완료는 아니다.
기존 PR #20의 입력/근거 provenance 보완에 함께 포함한다.

## 변경과 검증

- 승인된 `active_place_entrance` 조회로 만든 `VerifiedEntrance.place_id`를
  `place_entrance` source fact의 `value.place_id`로 함께 전달한다.
- `entrance_id`만 있던 기존 값에 canonical 장소 ID를 추가하며 좌표·geometry·사용자 원문은
  추가하지 않는다. 공개 Pydantic EvidenceFact의 기존 value 범위 안의 값이며 별도 Schema를 만들지 않는다.
- First RED: 새 gateway 테스트에서 `place_id` 누락으로 실패했다.
- 최소 GREEN: 조회 결과의 장소 ID를 복사했다. 실제 source fact와 source_refs를 검사한다.
- 최초 정적 검사에서 fixture의 list/tuple 타입 불일치를 발견해 실제 캐시 타입인 tuple로 수정했다.
- 한글 테스트 목적 검사·Ruff·Pyright 성공. 전체 pytest 596 passed, 9 skipped,
  기존 Starlette httpx deprecation warning 1건이다. Schema/manifest/합성 산출물 drift 테스트도 포함한다.
- 독립 부분 리뷰에서 신규 차단 0건이다. 전체 BE 승인 또는 배포 증거는 아니다.

## 후속 연결

- BE는 승인된 source fact의 entrance_id/place_id 연결과 해당 이동의 fact 참조를 함께 검증해야 한다.
  임의 입구 ID prefix를 장소 이름처럼 해석하지 않는다.
- JSON Schema hash는 바뀌지 않으므로 hash 일치만으로 새 근거 필드 지원을 추정할 수 없다.
  BE strict 검증을 활성화하기 전에 이 AI 변경을 함께 배포하고 실제 응답을 확인해야 한다.
- 대표좌표 endpoint와 검증 입구는 계속 구분한다. 기존 합성 버스 시각 불일치와 이동 구간
  상세 검증, BE 실제 후보 writer/조회/원자 적용·FE 연결은 별도 후속 작업이다.

## 합성 버스 시각 정합성 후속 수정

- v0.7 합성 버스는 접근5·대기15·승차15·하차도보5분이므로 전체40분이다.
  기존 종료09:45와 구성합계09:40 불일치의 회귀 RED를 확인했다.
- 공유 합성 생성기의 생성/평가 duration을 모두40분, mode decision 대기를15분으로 수정했다.
  대기는 실제 runtime의 예정출발-접근도보완료 정의를 따른다.
- v0.7 생성기로 산출물만 재생성했다. 보관된 v0.5/v0.6 파일은 변경하지 않았다.
  최초 전체 검사에서 checksum drift를 발견해 공식 manifest 생성기로 함께 갱신했다.
- 최종 한글 목적 검사·Ruff·Pyright 성공, pytest597 passed/9 skipped/기존 warning1이다.
  독립 부분 리뷰 신규 차단0이며 실제 운영 데이터나 BE 전체 연결 완료를 의미하지 않는다.
- 입구 fact가 없는 합성 endpoint를 실제 승인 입구처럼 쓰는 문제와 BE 이동 상세·후보 writer는
  여전히 후속이다. 이번 수정으로 시간·대기 정합성만 해결했다.
