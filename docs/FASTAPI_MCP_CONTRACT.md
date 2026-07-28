# 타이밍제주 FastAPI MCP 내부 계약 v1.1

## 1. 목적

이 문서는 FastAPI MCP 개발자가 담당할 계산/AI 범위와 [Spring 백엔드](https://github.com/Timing-Jeju/jeju_BE) 사이의 내부 계약을 정의한다. 프런트엔드는 MCP 서버를 직접 호출하지 않는다.

| 항목 | 값 |
| --- | --- |
| 호출자 | Spring Boot만 허용 |
| 전송 | Stateless MCP Streamable HTTP, `POST /mcp`, JSON response |
| 인증 | private network + 5분 이내 내부 service JWT |
| DB 접근 | 금지 |
| 외부 API 접근 | 금지 |
| 결과 저장 | Spring Boot |
| 기본 시간대 | `Asia/Seoul` |
| JSON 정책 | camelCase, 알 수 없는 필드 거절 |

## 2. 역할 분리

### Spring Boot 책임

- Supabase JWT 검증과 사용자/여행 소유권 확인.
- TourAPI, TAGO, 제주 버스 보조 데이터, 기상청, 길찾기 API 호출.
- 캐시 freshness와 fallback 결정.
- DB에서 여행, 일정 버전, 장소, 교통, 날씨 facts 조회.
- MCP 입력 snapshot 생성과 `inputHash` 계산.
- MCP 응답 JSON Schema 검증.
- 응답 ID가 입력 facts에 포함됐는지 참조 무결성 검증.
- DB UUID 생성, 트랜잭션, 일정 버전 봉인/적용.
- 재시도, timeout, circuit breaker, 감사 로그.

### FastAPI MCP 책임

- 구조화 입력 기반 Day 일정 후보 생성.
- 일정 시간/운영시간/교통/숙소/항공·선박 제약 검증.
- 이동시간, 대기시간, 버퍼, 가능성, 위험도 계산.
- 빈 시간 장소 추천과 순위 계산.
- 기존 일정을 최대한 보존하는 복구안 생성.
- 현재 위치/시각 기반 라이브 재계산.
- 계산 근거를 reason code와 수치 facts로 반환.
- 선택적으로 계산 결과의 자연어 설명 생성.
- 2차 범위에서 대화 입력을 구조화된 여행 조건으로 파싱.

### FastAPI MCP 금지 사항

- Supabase/Postgres를 직접 읽거나 쓰지 않는다.
- TourAPI/TAGO/KMA/지도 API를 직접 호출하지 않는다.
- 입력에 없는 `placeId`, `stopId`, `routeId`를 반환하지 않는다.
- 활성 일정을 직접 변경하거나 적용 여부를 결정하지 않는다.
- LLM이 계산한 숫자를 검증 없이 위험도 결과로 사용하지 않는다.
- OAuth token, Supabase JWT, 사용자 이메일을 입력으로 요구하지 않는다.

## 3. 도구 목록

| Tool | Phase | 성격 | 입력 | 출력 |
| --- | --- | --- | --- | --- |
| `generate_day_itinerary` | 1 | 생성+최적화 | Day 조건과 검증된 facts | 완전한 Day 후보 |
| `revise_day_itinerary` | 1 | 생성+최적화 | 기존 일정과 수정 지시 | 변경안과 diff |
| `validate_itinerary` | 1 | 결정론 | 일정/시간/운영 facts | 오류, 경고, 정규화 결과 |
| `calculate_feasibility` | 1 | 결정론 | 일정, 이동, 버스, 날씨 | 점수, 위험 이벤트, 날씨 영향 |
| `recommend_spare_time` | 1 | 결정론+순위 | 빈 시간과 장소 후보 | 삽입 가능한 추천 후보 |
| `generate_recovery_options` | 1 | 결정론+탐색 | 위험/실행 상태/전체 일정 | 완전한 복구 일정과 diff |
| `recalculate_live_state` | 1 | 결정론 | 현재 위치/시각/최신 facts | 다음 행동, 새 위험, 복구 필요 여부 |
| `explain_result` | 1 | 선택적 LLM | 확정된 계산 결과 | 숫자를 바꾸지 않는 설명 |
| `parse_trip_intent` | 2 | LLM+구조화 | 대화 메시지 | 구조화 조건과 확인 질문 |

### 3.1 Spring MCP Client와 wire 계약

- Spring은 `spring-ai-starter-mcp-client-webflux`로 FastAPI의 `/mcp`에 연결한다.
- FastAPI는 공식 Python SDK의 `FastMCP(stateless_http=True, json_response=True)`를 사용한다.
- Spring SDK가 `initialize`, protocol negotiation, `tools/list`, `tools/call` JSON-RPC를 처리한다.
- tool arguments는 이 문서의 Request이고 계산 결과는 MCP `result.structuredContent`에 이 문서의 Response로 반환한다.
- `content[].text`는 운영자용 짧은 요약일 뿐, Spring이 저장하는 계산 계약으로 사용하지 않는다.
- 예상 가능한 facts 부족/불가능 일정은 `structuredContent.status=failed`, MCP `isError=false`로 반환한다.
- 처리되지 않은 예외만 MCP `isError=true` 또는 JSON-RPC `error`로 반환한다.
- Supabase 사용자 JWT는 전달하지 않고 `aud=timing-jeju-fastapi`인 내부 service JWT만 전송한다.

실제 HTTP header, JSON-RPC `tools/call` request/response, Spring 저장 매핑은 [Spring-FastAPI MCP 내부 연동 명세](https://github.com/Timing-Jeju/jeju_BE/blob/develop/docs/designs/timing-jeju-spring-fastapi-integration-contract.md)에 정의한다.

## 4. 공통 요청 Envelope

```json
{
  "contractVersion": "feasibility.v1",
  "requestId": "01JZQ4E75EV56G3Q7QH5Q9YD82",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T09:30:00+09:00",
  "factsAsOf": "2026-08-03T09:29:58+09:00",
  "inputHash": "sha256:963c1e2c...",
  "trace": {
    "traceId": "01JZQ4E73A6Q2W0KJAV5M88A3B",
    "attempt": 1
  }
}
```

## 5. 공통 성공 응답 Envelope

```json
{
  "contractVersion": "feasibility.v1",
  "requestId": "01JZQ4E75EV56G3Q7QH5Q9YD82",
  "status": "succeeded",
  "algorithmVersion": "risk-engine-2026-07",
  "model": null,
  "inputHash": "sha256:963c1e2c...",
  "warnings": [],
  "computedAt": "2026-08-03T09:30:00.086+09:00"
}
```

`contractVersion`과 `inputHash`가 요청과 다르면 Spring은 응답을 폐기한다.

## 6. 공통 실패 응답

```json
{
  "contractVersion": "feasibility.v1",
  "requestId": "01JZQ4E75EV56G3Q7QH5Q9YD82",
  "status": "failed",
  "error": {
    "code": "INSUFFICIENT_TRANSIT_FACTS",
    "message": "성산일출봉에서 섭지코지로 이동할 교통 facts가 없습니다.",
    "retryable": false,
    "fieldPath": "facts.mobilityOptions"
  },
  "computedAt": "2026-08-03T09:30:00.010+09:00"
}
```

| Code | Retry | 의미 |
| --- | --- | --- |
| `CONTRACT_VERSION_UNSUPPORTED` | 아니오 | 지원하지 않는 계약 버전 |
| `INPUT_SCHEMA_INVALID` | 아니오 | 필수값/형식 오류 |
| `INPUT_HASH_MISMATCH` | 아니오 | envelope 무결성 오류 |
| `UNKNOWN_FACT_REFERENCE` | 아니오 | 입력에 없는 ID 참조 |
| `INSUFFICIENT_PLACE_FACTS` | 아니오 | 장소/운영시간 부족 |
| `INSUFFICIENT_TRANSIT_FACTS` | 조건부 | 이동/버스 facts 부족 |
| `INSUFFICIENT_WEATHER_FACTS` | 조건부 | 날씨 facts 부족 |
| `NO_FEASIBLE_SCHEDULE` | 아니오 | hard constraint를 만족하는 후보 없음 |
| `COMPUTE_TIMEOUT` | 예 | 계산 제한 시간 초과 |
| `MODEL_UNAVAILABLE` | 예 | 설명/대화 모델 장애 |
| `INTERNAL_COMPUTE_ERROR` | 예 | 예상하지 못한 내부 오류 |

## 7. 공통 Facts 구조

```json
{
  "trip": {
    "startDate": "2026-08-03",
    "endDate": "2026-08-05",
    "userPace": "normal",
    "preferredTransportModes": [
      "public_transit",
      "rental_car",
      "taxi"
    ]
  },
  "days": [
    {
      "dayRef": "day-1",
      "dayNo": 1,
      "date": "2026-08-03",
      "startTime": "09:00",
      "endTime": "21:00"
    }
  ],
  "places": [
    {
      "placeId": "20000000-0000-0000-0000-000000000002",
      "name": "성산일출봉",
      "category": "tourist_attraction",
      "location": {
        "lat": 33.458111,
        "lng": 126.941516
      },
      "recommendedStayMinutes": 70,
      "operatingWindows": [
        {
          "date": "2026-08-03",
          "openAt": "07:30",
          "closeAt": "20:00",
          "lastEntryAt": "19:00",
          "confidence": 0.85
        }
      ]
    }
  ],
  "mobilityOptions": [
    {
      "mobilityFactId": "mobility-airport-seongsan-1",
      "fromPlaceId": "20000000-0000-0000-0000-000000000001",
      "toPlaceId": "20000000-0000-0000-0000-000000000002",
      "mode": "public_transit",
      "walkMinutes": 8,
      "waitMinutes": 12,
      "rideMinutes": 80,
      "transferMinutes": 5,
      "totalMinutes": 105,
      "estimatedFare": 3000,
      "observedAt": "2026-08-03T09:29:40+09:00",
      "expiresAt": "2026-08-03T09:30:10+09:00",
      "confidence": 0.9
    }
  ],
  "weather": [
    {
      "weatherFactId": "weather-seongsan-1400",
      "placeId": "20000000-0000-0000-0000-000000000003",
      "validAt": "2026-08-03T14:00:00+09:00",
      "precipitationProbabilityPercent": 60,
      "precipitationAmountMm": 1.5,
      "windSpeedMps": 6.1,
      "temperatureC": 25.8,
      "forecastedAt": "2026-08-03T08:00:00+09:00"
    }
  ]
}
```

Spring은 원천 응답 전체를 보내지 않고 계산에 필요한 정규화 facts만 보낸다. `raw_payload`는 DB 감사용이며 MCP 입력에서 제외한다.

## 8. `generate_day_itinerary`

### 목적

구조화 입력과 검증된 facts로 특정 Day의 적용 가능한 완전한 일정 후보를 생성한다. Day 일부만 생성하더라도 응답은 해당 Day의 모든 항목과 인접 이동 구간을 포함한다.

### Request

```json
{
  "contractVersion": "itinerary-generation.v1",
  "requestId": "01JZQ4GQ15JSQG3C3WHBPT4S4X",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T09:25:00+09:00",
  "factsAsOf": "2026-08-03T09:24:58+09:00",
  "inputHash": "sha256:generation-input",
  "trace": {
    "traceId": "01JZQ4GQ0HJDVFM9N3DYGW4G0R",
    "attempt": 1
  },
  "targetDay": {
    "dayRef": "day-1",
    "dayNo": 1,
    "date": "2026-08-03",
    "startAt": "2026-08-03T09:00:00+09:00",
    "endAt": "2026-08-03T21:00:00+09:00"
  },
  "constraints": {
    "mustVisitPlaceIds": [
      "20000000-0000-0000-0000-000000000002"
    ],
    "optionalPlaceIds": [
      "20000000-0000-0000-0000-000000000003"
    ],
    "startPlaceId": "20000000-0000-0000-0000-000000000001",
    "endPlaceId": "20000000-0000-0000-0000-000000000004",
    "transportModes": [
      "public_transit",
      "taxi"
    ],
    "userPace": "normal",
    "minimumBufferMinutes": 10,
    "candidateCount": 3
  },
  "facts": {
    "places": [],
    "mobilityOptions": [],
    "weather": [],
    "transportEvents": [],
    "accommodations": []
  }
}
```

### Response

```json
{
  "contractVersion": "itinerary-generation.v1",
  "requestId": "01JZQ4GQ15JSQG3C3WHBPT4S4X",
  "status": "succeeded",
  "algorithmVersion": "scheduler-2026-07",
  "model": "optional-ranking-model",
  "inputHash": "sha256:generation-input",
  "warnings": [],
  "candidates": [
    {
      "candidateRef": "candidate-1",
      "rank": 1,
      "score": 88,
      "riskLevel": "green",
      "summary": "섭지코지를 먼저 방문해 버스 대기 위험을 줄였습니다.",
      "items": [
        {
          "clientRef": "item-arrival",
          "sequenceNo": 1,
          "itemType": "arrival",
          "placeId": "20000000-0000-0000-0000-000000000001",
          "title": "제주 도착",
          "plannedStartAt": "2026-08-03T09:00:00+09:00",
          "plannedEndAt": "2026-08-03T09:20:00+09:00",
          "stayMinutes": 20,
          "bufferAfterMinutes": 10,
          "required": true,
          "reasonCodes": [
            "ARRIVAL_EVENT_FIXED"
          ]
        },
        {
          "clientRef": "item-seopji",
          "sequenceNo": 2,
          "itemType": "place_visit",
          "placeId": "20000000-0000-0000-0000-000000000003",
          "title": "섭지코지",
          "plannedStartAt": "2026-08-03T10:50:00+09:00",
          "plannedEndAt": "2026-08-03T11:50:00+09:00",
          "stayMinutes": 60,
          "bufferAfterMinutes": 10,
          "required": false,
          "reasonCodes": [
            "LOWER_TRANSIT_WAIT"
          ]
        }
      ],
      "legs": [
        {
          "clientRef": "leg-arrival-seopji",
          "sequenceNo": 1,
          "fromItemRef": "item-arrival",
          "toItemRef": "item-seopji",
          "mobilityFactId": "mobility-airport-seopji-1",
          "transportMode": "public_transit",
          "plannedDepartureAt": "2026-08-03T09:30:00+09:00",
          "plannedArrivalAt": "2026-08-03T10:50:00+09:00",
          "walkMinutes": 8,
          "waitMinutes": 12,
          "rideMinutes": 60,
          "transferMinutes": 0,
          "bufferMinutes": 10,
          "durationMinutes": 80
        }
      ],
      "metrics": {
        "totalStayMinutes": 150,
        "totalTravelMinutes": 125,
        "totalBufferMinutes": 40,
        "changedRequiredItemCount": 0
      }
    }
  ],
  "computedAt": "2026-08-03T09:25:01.320+09:00"
}
```

### 검증 규칙

- 모든 필수 장소와 고정 transport event/accommodation을 보존한다.
- `clientRef`는 응답 안에서 유일해야 한다.
- `fromItemRef`, `toItemRef`는 같은 후보의 item을 참조해야 한다.
- 모든 item은 장소 또는 명시적 좌표, 시작/종료 시각, 양수 체류시간을 가져야 한다.
- 각 Day의 item 순번은 1부터 연속이어야 하며 인접한 모든 item 사이에 완전한 leg가 있어야 한다. leg의 출발·도착 시간차, `durationMinutes`, 도보·대기·탑승·환승 합계는 일치해야 한다.
- 장소 운영시간, Day 범위, 최소 버퍼를 위반한 후보를 반환하지 않는다.
- 조건을 만족하는 후보가 없으면 억지 후보 대신 `NO_FEASIBLE_SCHEDULE`을 반환한다.

## 9. `revise_day_itinerary`

### Request

```json
{
  "contractVersion": "itinerary-revision.v1",
  "requestId": "01JZQ4J7Q1H2B9C4Q0G4YHXXY7",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T09:40:00+09:00",
  "factsAsOf": "2026-08-03T09:39:58+09:00",
  "inputHash": "sha256:revision-input",
  "trace": {
    "traceId": "01JZQ4J7PS5AW6GN48QX3M99TM",
    "attempt": 1
  },
  "instruction": {
    "type": "shorten_stay",
    "targetItemRef": "item-seopji",
    "stayMinutes": 45
  },
  "currentDaySchedule": {
    "items": [],
    "legs": []
  },
  "facts": {
    "places": [],
    "mobilityOptions": [],
    "weather": []
  }
}
```

### Response

```json
{
  "contractVersion": "itinerary-revision.v1",
  "requestId": "01JZQ4J7Q1H2B9C4Q0G4YHXXY7",
  "status": "succeeded",
  "algorithmVersion": "scheduler-2026-07",
  "model": null,
  "inputHash": "sha256:revision-input",
  "warnings": [],
  "candidate": {
    "candidateRef": "revision-1",
    "items": [],
    "legs": [],
    "changes": [
      {
        "order": 1,
        "action": "shorten_stay",
        "sourceItemRef": "item-seopji",
        "proposedItemRef": "item-seopji-revised",
        "before": {
          "stayMinutes": 60
        },
        "after": {
          "stayMinutes": 45
        },
        "reasonCode": "USER_REQUESTED_STAY_CHANGE"
      }
    ]
  },
  "computedAt": "2026-08-03T09:40:00.091+09:00"
}
```

응답 `items`, `legs`는 변경분만이 아니라 해당 Day 전체를 반환한다.

## 10. `validate_itinerary`

### Request

```json
{
  "contractVersion": "itinerary-validation.v1",
  "requestId": "01JZQ4KZ3MBYGC22N2H8XG5Y8J",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "draft-client-version-1",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T09:42:00+09:00",
  "factsAsOf": "2026-08-03T09:41:58+09:00",
  "inputHash": "sha256:validation-input",
  "trace": {
    "traceId": "01JZQ4KZ2ZDGWXK7C7V9W1KRZ4",
    "attempt": 1
  },
  "schedule": {
    "days": [
      {
        "dayNo": 1,
        "date": "2026-08-03",
        "items": [],
        "legs": []
      }
    ]
  },
  "facts": {
    "places": [],
    "transportEvents": [],
    "accommodations": []
  }
}
```

### Response

```json
{
  "contractVersion": "itinerary-validation.v1",
  "requestId": "01JZQ4KZ3MBYGC22N2H8XG5Y8J",
  "status": "succeeded",
  "algorithmVersion": "validator-2026-07",
  "model": null,
  "inputHash": "sha256:validation-input",
  "warnings": [],
  "valid": false,
  "violations": [
    {
      "level": "error",
      "code": "PLACE_CLOSED_AT_VISIT_TIME",
      "itemRef": "item-seopji",
      "legRef": null,
      "message": "섭지코지 운영 종료 후 방문으로 설정되었습니다.",
      "facts": {
        "plannedStartAt": "2026-08-03T18:30:00+09:00",
        "closeAt": "2026-08-03T18:00:00+09:00"
      }
    }
  ],
  "computedAt": "2026-08-03T09:42:00.020+09:00"
}
```

Spring의 FK, 범위, 타입 검증은 항상 수행한다. 이 tool은 시간·운영·여행 정책 검증을 담당한다.

## 11. `calculate_feasibility`

### Request

```json
{
  "contractVersion": "feasibility.v1",
  "requestId": "01JZQ4N3A0TJY0ZBAMXXR5PYS1",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T09:30:00+09:00",
  "factsAsOf": "2026-08-03T09:29:58+09:00",
  "inputHash": "sha256:feasibility-input",
  "trace": {
    "traceId": "01JZQ4N39H57RPX2GGCBBMNFRY",
    "attempt": 1
  },
  "dayNo": 1,
  "schedule": {
    "items": [],
    "legs": []
  },
  "facts": {
    "places": [],
    "mobilityOptions": [],
    "busArrivals": [],
    "weather": [],
    "transportEvents": [],
    "accommodations": []
  },
  "policy": {
    "minimumSafeMarginMinutes": 15,
    "staleArrivalThresholdSeconds": 60,
    "unknownFactBehavior": "caution"
  }
}
```

### Response

```json
{
  "contractVersion": "feasibility.v1",
  "requestId": "01JZQ4N3A0TJY0ZBAMXXR5PYS1",
  "status": "succeeded",
  "algorithmVersion": "risk-engine-2026-07",
  "model": null,
  "inputHash": "sha256:feasibility-input",
  "warnings": [],
  "result": {
    "overallLevel": "yellow",
    "score": 81,
    "summaryCode": "TRANSIT_WAIT_AND_RAIN",
    "reasonCodes": [
      "LOW_FREQUENCY_ROUTE",
      "RAIN_RISK"
    ],
    "legResults": [
      {
        "legRef": "62000000-0000-0000-0000-000000000002",
        "level": "yellow",
        "leaveByTime": "2026-08-03T12:40:00+09:00",
        "walkMinutes": 10,
        "waitMinutes": 22,
        "rideMinutes": 8,
        "transferMinutes": 0,
        "availableMinutes": 50,
        "requiredMinutes": 40,
        "marginMinutes": 10,
        "reasonCodes": [
          "LOW_FREQUENCY_ROUTE"
        ]
      }
    ],
    "riskEvents": [
      {
        "clientRef": "risk-low-frequency-1",
        "tripItemRef": null,
        "tripLegRef": "62000000-0000-0000-0000-000000000002",
        "eventType": "low_frequency",
        "severity": "yellow",
        "scoreDelta": -19,
        "waitRiskMinutes": 42,
        "reasonCode": "LOW_FREQUENCY_ROUTE",
        "computedFacts": {
          "routeNo": "201",
          "missedBusWaitMinutes": 42
        }
      }
    ],
    "weatherImpacts": [
      {
        "clientRef": "weather-rain-1",
        "tripItemRef": "61000000-0000-0000-0000-000000000003",
        "tripLegRef": null,
        "weatherFactId": "weather-seongsan-1400",
        "impactType": "rain",
        "severity": "yellow",
        "scoreDelta": -8,
        "reasonCode": "RAIN_RISK",
        "computedFacts": {
          "precipitationProbabilityPercent": 60,
          "precipitationAmountMm": 1.5
        }
      }
    ]
  },
  "computedAt": "2026-08-03T09:30:00.086+09:00"
}
```

## 12. 가능성/위험도 정책 v1

### 12.1 이동 여유시간

```text
availableMinutes = nextItem.start - currentItem.end
requiredMinutes = walk + wait + ride + transfer + operationalBuffer
marginMinutes = availableMinutes - requiredMinutes
```

| 조건 | Level | Reason |
| --- | --- | --- |
| `marginMinutes < 0` | `red` | `IMPOSSIBLE_SEGMENT` |
| `0 <= marginMinutes < 15` | `yellow` | `TIGHT_TRANSFER` |
| `marginMinutes >= 15` | `green` | `SUFFICIENT_MARGIN` |

### 12.2 Hard constraint

아래 중 하나면 해당 Day는 `red`다.

- 다음 장소 도착이 운영 종료/마지막 입장 이후다.
- 항공/선박 출발 안전 도착 시각을 지킬 수 없다.
- 숙소 체크인/체크아웃과 일정이 충돌한다.
- 선택한 교통수단으로 연결 가능한 이동 facts가 없다.
- 필수 장소를 Day 범위 안에 배치할 수 없다.

### 12.3 버스 facts 우선순위

1. TTL 안의 실시간 도착 snapshot.
2. 정적 시간표와 운행일 유형.
3. 최근 캐시 fallback. 사용 시 `STALE_TRANSIT_DATA`를 추가한다.
4. 자료가 없으면 시간을 추측하지 않고 `INSUFFICIENT_TRANSIT_FACTS`를 반환한다.

### 12.4 날씨 기준 초기값

| 조건 | 기본 영향 |
| --- | --- |
| 강수확률 `>= 60%` 또는 강수량 `>= 1mm` | `RAIN_RISK`, 도보 버퍼 +10분 |
| 풍속 `>= 9m/s` | `HIGH_WIND`, 야외 장소 주의 |
| 풍속 `>= 14m/s` | `WEATHER_WARNING`, 야외 구간 위험 |
| 기온 `>= 33C` | `HEAT_RISK`, 야외 연속 체류 감점 |
| 기온 `<= 0C` | `COLD_RISK`, 도보 구간 감점 |

임계값은 `algorithmVersion`별 설정으로 관리하고 코드에 흩어 놓지 않는다.

### 12.5 Score

- 기본 100점에서 위험 이벤트의 `scoreDelta`를 합산하고 `0..100`으로 제한한다.
- `red` hard constraint가 있으면 최대 49점이다.
- `yellow` 이벤트가 하나라도 있으면 최대 79점이다.
- 수치 점수와 색상 level이 모순되지 않게 마지막에 정규화한다.

## 13. `recommend_spare_time`

### Request

```json
{
  "contractVersion": "spare-time.v1",
  "requestId": "01JZQ4R7B7Q5RF64T4F9JQV3GA",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T12:00:00+09:00",
  "factsAsOf": "2026-08-03T11:59:58+09:00",
  "inputHash": "sha256:spare-time-input",
  "trace": {
    "traceId": "01JZQ4R7AJH1M7FY0ZB11MW3YZ",
    "attempt": 1
  },
  "gap": {
    "dayNo": 1,
    "afterItemRef": "61000000-0000-0000-0000-000000000002",
    "startsAt": "2026-08-03T12:40:00+09:00",
    "endsAt": "2026-08-03T14:10:00+09:00",
    "availableMinutes": 90
  },
  "candidatePlaceIds": [
    "20000000-0000-0000-0000-000000000006"
  ],
  "facts": {
    "places": [],
    "mobilityOptions": [],
    "weather": []
  },
  "maxCandidates": 10
}
```

### Response

```json
{
  "contractVersion": "spare-time.v1",
  "requestId": "01JZQ4R7B7Q5RF64T4F9JQV3GA",
  "status": "succeeded",
  "algorithmVersion": "recommendation-engine-2026-07",
  "model": null,
  "inputHash": "sha256:spare-time-input",
  "warnings": [],
  "recommendations": [
    {
      "clientRef": "recommendation-cafe-1",
      "candidatePlaceId": "20000000-0000-0000-0000-000000000006",
      "recommendationType": "spare_time",
      "availableGapMinutes": 90,
      "travelMinutes": 10,
      "stayMinutes": 45,
      "safetyBufferMinutes": 10,
      "requiredTotalMinutes": 65,
      "score": 78,
      "reasonCode": "PLACE_FITS_GAP",
      "computedFacts": {
        "remainingMinutes": 25,
        "weatherPenalty": 0
      }
    }
  ],
  "computedAt": "2026-08-03T12:00:00.041+09:00"
}
```

추천은 `travel + stay + safetyBuffer <= availableGap`인 후보만 반환한다.

## 14. `generate_recovery_options`

### Request

```json
{
  "contractVersion": "recovery.v1",
  "requestId": "01JZQ4T9WJFKP0X7D5P3YBJD75",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T12:45:00+09:00",
  "factsAsOf": "2026-08-03T12:44:58+09:00",
  "inputHash": "sha256:recovery-input",
  "trace": {
    "traceId": "01JZQ4T9W2T6G7AFZDHJ60D7FJ",
    "attempt": 1
  },
  "trigger": {
    "riskEventRef": "63100000-0000-0000-0000-000000000001",
    "reasonCode": "LOW_FREQUENCY_ROUTE",
    "currentTime": "2026-08-03T12:45:00+09:00",
    "currentLocation": {
      "lat": 33.458111,
      "lng": 126.941516
    }
  },
  "baseSchedule": {
    "days": []
  },
  "progress": [],
  "facts": {
    "places": [],
    "mobilityOptions": [],
    "busArrivals": [],
    "weather": [],
    "transportEvents": [],
    "accommodations": []
  },
  "policy": {
    "preserveRequiredItems": true,
    "preserveCompletedItems": true,
    "preserveOriginalOrder": true,
    "allowAutomaticReorder": false,
    "maxChangedItems": 4,
    "maxOptions": 3
  }
}
```

### Response

```json
{
  "contractVersion": "recovery.v1",
  "requestId": "01JZQ4T9WJFKP0X7D5P3YBJD75",
  "status": "succeeded",
  "algorithmVersion": "recovery-engine-2026-07",
  "model": null,
  "inputHash": "sha256:recovery-input",
  "warnings": [],
  "options": [
    {
      "optionRef": "recovery-option-1",
      "optionType": "move_to_another_day",
      "titleCode": "MOVE_PLACE_TO_NEXT_DAY",
      "impactMinutes": 20,
      "resultingLevel": "green",
      "resultingScore": 90,
      "schedule": {
        "days": []
      },
      "changes": [
        {
          "order": 1,
          "action": "move_day",
          "sourceItemRef": "61000000-0000-0000-0000-000000000003",
          "proposedItemRef": "proposed-seopji-day-2",
          "before": {
            "dayNo": 1,
            "startTime": "13:20"
          },
          "after": {
            "dayNo": 2,
            "startTime": "10:20"
          },
          "reasonCode": "AVOID_LOW_FREQUENCY_ROUTE"
        }
      ],
      "preservation": {
        "requiredItemsPreserved": true,
        "completedItemsPreserved": true,
        "relativeOrderPreserved": true,
        "automaticReorderApplied": false,
        "changedItemCount": 1
      }
    }
  ],
  "computedAt": "2026-08-03T12:45:00.114+09:00"
}
```

### 복구안 정렬 기준

1. 이미 완료한 항목과 고정 항공/선박 이벤트를 보존한다.
2. `required=true` 장소를 보존한다.
3. 숙소 날짜와 체크인 조건을 보존한다.
4. 같은 Day에 남는 항목의 기존 상대 순서를 보존한다.
5. 변경 항목 수를 최소화한다.
6. 위험 level과 점수를 개선한다.
7. 추가 비용과 총 지연을 최소화한다.

동률이면 `changedItemCount`, `impactMinutes`, `estimatedAdditionalFare` 순으로 정렬한다.

자동 순서 재배치는 복구안에서 제외한다. 사용자가 직접 순서를 바꾸는 기능은 Spring 일정 수정 API가 새 버전을 만드는 별도 흐름으로 처리한다. 모든 복구 후보도 위치/시간/체류시간과 인접 이동 구간을 빠짐없이 포함해야 한다.

## 15. `recalculate_live_state`

### Request

```json
{
  "contractVersion": "live-recalculation.v1",
  "requestId": "01JZQ4WCR8C7P80ZC8J5Z7XR7D",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T13:30:00+09:00",
  "factsAsOf": "2026-08-03T13:29:58+09:00",
  "inputHash": "sha256:live-input",
  "trace": {
    "traceId": "01JZQ4WCQZ2WQ80M85X4C6RNGD",
    "attempt": 1
  },
  "trigger": "missed",
  "currentTime": "2026-08-03T13:30:00+09:00",
  "currentLocation": {
    "lat": 33.458111,
    "lng": 126.941516,
    "accuracyMeters": 18
  },
  "activeItemRef": "61000000-0000-0000-0000-000000000002",
  "activeLegRef": "62000000-0000-0000-0000-000000000002",
  "schedule": {
    "days": []
  },
  "progress": [],
  "facts": {
    "mobilityOptions": [],
    "busArrivals": [],
    "weather": []
  }
}
```

### Response

```json
{
  "contractVersion": "live-recalculation.v1",
  "requestId": "01JZQ4WCR8C7P80ZC8J5Z7XR7D",
  "status": "succeeded",
  "algorithmVersion": "live-engine-2026-07",
  "model": null,
  "inputHash": "sha256:live-input",
  "warnings": [],
  "liveState": {
    "level": "red",
    "nextActionCode": "SELECT_RECOVERY_OPTION",
    "leaveByTime": null,
    "canStayMoreMinutes": 0,
    "activeItemRef": "61000000-0000-0000-0000-000000000002",
    "activeLegRef": "62000000-0000-0000-0000-000000000002",
    "facts": {
      "nextBusWaitMinutes": 42,
      "remainingDayMinutes": 450
    }
  },
  "riskEvents": [
    {
      "clientRef": "risk-missed-bus-1",
      "eventType": "missed_bus",
      "severity": "red",
      "tripLegRef": "62000000-0000-0000-0000-000000000002",
      "reasonCode": "MISSED_LAST_FEASIBLE_BUS",
      "scoreDelta": -35
    }
  ],
  "recoveryRequired": true,
  "computedAt": "2026-08-03T13:30:00.076+09:00"
}
```

복구안 상세 일정은 `generate_recovery_options`를 별도로 호출해 생성한다.

## 16. `explain_result`

이 도구는 수치 계산 후 사용자용 설명이 필요할 때만 호출한다. 모델이 실패해도 Spring은 reason code 템플릿으로 fallback할 수 있어야 한다.

### Request

```json
{
  "contractVersion": "explanation.v1",
  "requestId": "01JZQ4Y4MZ2HFJ7JPA4DW3X1QK",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": "60000000-0000-0000-0000-000000000001",
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T13:31:00+09:00",
  "factsAsOf": "2026-08-03T13:30:00+09:00",
  "inputHash": "sha256:explanation-input",
  "trace": {
    "traceId": "01JZQ4Y4MECBN89GQZ3D5V62VB",
    "attempt": 1
  },
  "locale": "ko-KR",
  "result": {
    "level": "yellow",
    "score": 81,
    "reasonCodes": [
      "LOW_FREQUENCY_ROUTE",
      "RAIN_RISK"
    ],
    "numericFacts": {
      "busWaitMinutes": 22,
      "rainProbabilityPercent": 60
    }
  },
  "style": {
    "maxCharacters": 120,
    "tone": "clear_and_calm"
  }
}
```

### Response

```json
{
  "contractVersion": "explanation.v1",
  "requestId": "01JZQ4Y4MZ2HFJ7JPA4DW3X1QK",
  "status": "succeeded",
  "algorithmVersion": "explanation-2026-07",
  "model": "configured-language-model",
  "inputHash": "sha256:explanation-input",
  "warnings": [],
  "summary": "버스 대기 시간이 22분이고 비 올 확률이 60%라 일정에 여유가 적습니다.",
  "usedReasonCodes": [
    "LOW_FREQUENCY_ROUTE",
    "RAIN_RISK"
  ],
  "computedAt": "2026-08-03T13:31:00.420+09:00"
}
```

응답 문장에 입력에 없는 숫자, 장소, 교통편이 포함되면 Spring이 폐기한다.

## 17. `parse_trip_intent` Phase 2

### Request

```json
{
  "contractVersion": "trip-intent.v1",
  "requestId": "01JZQ50CQ98M1HX6G9S1GY2XXF",
  "tripId": "50000000-0000-0000-0000-000000000001",
  "scheduleVersionId": null,
  "timezone": "Asia/Seoul",
  "requestedAt": "2026-08-03T09:00:00+09:00",
  "factsAsOf": "2026-08-03T09:00:00+09:00",
  "inputHash": "sha256:intent-input",
  "trace": {
    "traceId": "01JZQ50CQ0S24VZFP59A0ZTY2M",
    "attempt": 1
  },
  "locale": "ko-KR",
  "conversation": [
    {
      "role": "user",
      "content": "8월 3일부터 2박 3일이고 성산일출봉은 꼭 가고 싶어. 버스 위주로 다닐래."
    }
  ],
  "placeCandidates": [
    {
      "query": "성산일출봉",
      "matches": [
        {
          "placeId": "20000000-0000-0000-0000-000000000002",
          "name": "성산일출봉",
          "confidence": 0.98
        }
      ]
    }
  ]
}
```

### Response

```json
{
  "contractVersion": "trip-intent.v1",
  "requestId": "01JZQ50CQ98M1HX6G9S1GY2XXF",
  "status": "succeeded",
  "algorithmVersion": "intent-parser-2026-07",
  "model": "configured-language-model",
  "inputHash": "sha256:intent-input",
  "warnings": [],
  "intent": {
    "startDate": "2026-08-03",
    "endDate": "2026-08-05",
    "transportModes": [
      "public_transit"
    ],
    "mustVisitPlaceIds": [
      "20000000-0000-0000-0000-000000000002"
    ],
    "preferredRegionCodes": [
      "seongsan"
    ]
  },
  "missingFields": [
    "arrivalTransportEvent",
    "departureTransportEvent",
    "accommodations"
  ],
  "confirmationQuestions": [
    {
      "field": "arrivalTransportEvent",
      "question": "제주에 도착하는 시간과 항공편 또는 배편을 알려주세요."
    }
  ],
  "computedAt": "2026-08-03T09:00:00.510+09:00"
}
```

## 18. Reason Code 기본 사전

| Category | Code | 의미 |
| --- | --- | --- |
| Schedule | `ITEM_TIME_OVERLAP` | 항목 시간이 겹침 |
| Schedule | `ITEM_OUTSIDE_DAY` | Day 범위 밖 |
| Place | `PLACE_CLOSED_AT_VISIT_TIME` | 운영시간 위반 |
| Place | `LAST_ENTRY_TIME_MISSED` | 마지막 입장시간 위반 |
| Transit | `IMPOSSIBLE_SEGMENT` | 이동 불가능 |
| Transit | `TIGHT_TRANSFER` | 이동 여유 15분 미만 |
| Transit | `LOW_FREQUENCY_ROUTE` | 놓치면 긴 대기 발생 |
| Transit | `STALE_TRANSIT_DATA` | TTL 지난 교통 facts 사용 |
| Transit | `LONG_WALK` | 정책 기준보다 긴 도보 |
| Event | `TRANSPORT_EVENT_MISS` | 항공/선박 안전 도착 실패 |
| Stay | `ACCOMMODATION_CONFLICT` | 숙소 날짜/시간 충돌 |
| Weather | `RAIN_RISK` | 강수 위험 |
| Weather | `HIGH_WIND` | 강풍 주의 |
| Weather | `HEAT_RISK` | 폭염 위험 |
| Weather | `COLD_RISK` | 한랭 위험 |
| Recovery | `AVOID_LOW_FREQUENCY_ROUTE` | 저빈도 노선 회피 |
| Recommend | `PLACE_FITS_GAP` | 빈 시간 내 방문 가능 |

코드는 append-only로 관리한다. 의미 변경이 필요하면 새 코드를 만든다.

## 19. 성능/운영 계약

| Tool | 목표 timeout | Spring 재시도 | 캐시 가능 |
| --- | --- | --- | --- |
| `validate_itinerary` | 2초 | 0 | inputHash 기준 |
| `calculate_feasibility` | 5초 | 1 | facts TTL 내 |
| `recommend_spare_time` | 5초 | 1 | facts TTL 내 |
| `recalculate_live_state` | 3초 | 1 | 불가 |
| `generate_day_itinerary` | 30초 | 0 | 동일 inputHash |
| `revise_day_itinerary` | 20초 | 0 | 동일 inputHash |
| `generate_recovery_options` | 10초 | 0 | 짧은 TTL |
| `explain_result` | 5초 | 0 | 동일 locale/result hash |
| `parse_trip_intent` | 10초 | 0 | 대화 hash 기준 |

- Spring의 retry는 같은 `requestId`와 `inputHash`, 증가한 `attempt`를 사용한다.
- FastAPI는 같은 `requestId` 재호출에 같은 완료 결과를 반환할 수 있어야 한다.
- 위치가 포함된 payload는 원문 로그에 남기지 않고 격자화/삭제한 redacted payload만 저장한다.
- 모델 prompt와 completion 원문은 기본 저장하지 않는다.

## 20. 계약 테스트

### FastAPI 단위/속성 테스트

- 같은 입력과 같은 algorithm version은 같은 결정론 결과를 낸다.
- 모든 반환 ID가 입력 facts 또는 응답 내부 `clientRef`에 존재한다.
- 생성 후보의 시간이 겹치지 않는다.
- 생성 후보가 운영시간, 숙소, 항공/선박 hard constraint를 지킨다.
- 추천 후보는 `requiredTotalMinutes <= availableGapMinutes`다.
- 복구안은 완료한 항목과 필수 항목을 보존한다.
- score와 level이 정책 범위를 만족한다.

### Spring-FastAPI 계약 테스트

- 각 tool의 JSON Schema를 양쪽 CI에서 같은 버전으로 검증한다.
- 지원하지 않는 `contractVersion`은 명시적 오류가 난다.
- timeout, malformed JSON, 알 수 없는 ID, 중복 `clientRef`를 Spring이 거절한다.
- FastAPI 장애 시 활성 일정과 DB 트랜잭션이 변경되지 않는다.
- 저장된 `mcp_compute_call_logs`에 request ID, contract/algorithm version, latency, redacted payload가 남는다.

## 21. 완료 기준

- FastAPI MCP의 모든 Phase 1 tool이 이 문서의 요청/응답 예시와 JSON Schema 계약을 만족한다.
- 숫자 계산은 reason code와 computed facts로 재현 가능하다.
- LLM이 없어도 가능성, 위험도, 추천, 복구, 라이브 계산이 동작한다.
- Spring만 결과를 DB에 저장하고 일정 후보를 적용한다.
