# 제주 동부 부분 데이터 후보 순서 preview

생성시각: 2026-08-11T07:59:41.489858+00:00

## 판정

- 상태: `staging_candidate_preview`
- 운영 추천 여부: `false`
- 부분 상세정보 완성률: 92.15%
- 대표 숙소: 플레이스 캠프 제주(Playce camp Jeju)
- 숙소 endpoint: `provisional_center_only`

## 후보 순서

- `balanced`: 성산일출봉 [유네스코 세계자연유산] → 광치기해변 → 성산 부뚜막식당 → 섭지코지 → 오조포구 → 호랑호랑 성산카페
- `relaxed`: 성산일출봉 [유네스코 세계자연유산] → 오조포구 → 성산 부뚜막식당 → 광치기해변 → 도렐 제주 본점 → 호랑호랑 성산카페
- `experience_max`: 성산일출봉 [유네스코 세계자연유산] → 섭지코지 → 성산 부뚜막식당 → 제주레일바이크 → 호랑호랑 성산카페

## 운영시간 상세 조회 상태

- 성산일출봉 [유네스코 세계자연유산]: `unparseable_from_partial_snapshot`
- 광치기해변: `unparseable_from_partial_snapshot`
- 성산 부뚜막식당: `unparseable_from_partial_snapshot`
- 섭지코지: `unparseable_from_partial_snapshot`
- 오조포구: `unparseable_from_partial_snapshot`
- 호랑호랑 성산카페: `unparseable_from_partial_snapshot`
- 도렐 제주 본점: `unparseable_from_partial_snapshot`
- 제주레일바이크: `source_query_missing`

## 아직 없는 근거

- `VERIFIED_ENTRANCE`
- `PUBLISHED_OPENING_HOURS`
- `DOOR_TO_DOOR_ROUTE_FACT`
- `FULL_SOURCE_SNAPSHOT`

## 해석

이미 적재된 TourAPI 장소만으로 세 전략의 화면 형태와 장소 순서 다양성을 먼저 확인한 결과다.
시간·거리·비용·가능 여부를 계산하지 않았고 공개 `recommend_jeju_day_trips` 성공 응답으로
사용할 수 없다. 외부 API를 추가 호출하지 않았으므로 TourAPI와 TMAP 사용량은 0건이다.
