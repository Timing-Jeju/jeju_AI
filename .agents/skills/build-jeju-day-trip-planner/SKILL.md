---
name: build-jeju-day-trip-planner
description: Build, review, and test the 제주 하루 여행 MCP planner using TourAPI, TAGO, 제주 버스 data, TMAP routes, LangChain/LangGraph, evidence-grounded JSON, raw-first dataset publication, door-to-stop walking, safety margins, and Korean-described tests. Use for 제주 여행 데이터 수집, 저장, 정규화, 일정 추천, MCP 도구, JSON 계약, 버스·도보 경로, provenance, 스케줄러, 테스트 및 구현 계획 작업.
---

# 제주 하루 여행 MCP 구현

## 작업 절차

1. `AGENTS.md`, `docs/PROJECT_PLAN.md`, `docs/JSON_CONTRACT.md`를 읽는다.
2. 요청을 데이터, 저장, 계획, JSON, MCP, 테스트 중 하나 이상으로 분류한다.
3. 공개 모델 변경 전후에 Pydantic 모델과 생성 Schema 영향을 확인한다.
4. 외부 호출 전에 source contract의 승인 상태, 허용 host/path, 보존 조건을 확인한다.
5. 실패해야 하는 가장 작은 한글 설명 테스트(First RED)를 먼저 정의하고 최소 GREEN을 구현한다.
6. LLM은 후보 순서·수선·설명에만 쓰고 시간·거리·비용·검증은 결정론적 코드에 둔다.
7. 모든 테스트 함수의 한글 docstring과 parametrized case의 한글 ID를 확인한다.
8. 아래 품질 명령을 순서대로 실행한다.
9. 결과를 `상태`, `검증 근거`, `검증 공백`, `잔여 위험`으로 보고한다.

## 필요한 참조 선택

- 제품 범위와 계층: [project-context.md](references/project-context.md)
- 수집·저장·정류장 ID·TMAP 정책: [data-storage-rules.md](references/data-storage-rules.md)
- 공개 응답·evidence·불변조건: [json-contract-rules.md](references/json-contract-rules.md)
- First RED와 품질 게이트: [testing-rules.md](references/testing-rules.md)

관련된 참조만 읽되, 선택한 파일은 끝까지 읽는다.

## 핵심 판정

- 성공은 `balanced`, `relaxed`, `experience_max`가 각각 한 개일 때만 허용한다.
- 모든 버스 이동에 장소 endpoint 기반 `access_walk`와 `egress_walk`가 있어야 한다.
  검증 입구가 없으면 active TourAPI 대표좌표를 provisional endpoint로 명시한다.
- 외부 사실과 계산값은 `evidence_fact` 및 `source_refs`로 추적 가능해야 한다.
- TMAP 응답은 최대 23시간 50분의 프로세스 메모리 캐시에만 둘 수 있다.
- 공식 시간표가 불명확하면 버스 후보를 검증 불가로 둔다. 운영시간이 불명확하면 값을
  추정하지 않고 `UNVERIFIED` 주의 상태로 생성·평가한다. 출입구 coverage는 일반 요청의
  품질 지표이며 이동보조·계단회피 필수 요청만 차단한다.
- 입력 원문보다 구조화 필드를 우선하고 원문 보충은 assumption으로 남긴다.

## 품질 명령

```text
uv run python scripts/check_korean_test_descriptions.py
uv run ruff check .
uv run pyright
uv run pytest
```
