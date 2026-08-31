# TourAPI 단건 staging 보충 결과

- 장소: 제주레일바이크
- 상태: `staging_supplement_unavailable`
- 실패 코드: `PAGINATION_INTERRUPTED`
- raw 저장: `false`
- 운영 활성화 허용: `false`

응답 페이지를 얻지 못해 raw acquisition을 만들지 않았다. 같은 실행에서 허용된 1회 재시도까지
끝났으므로 추가 호출하지 않는다.
