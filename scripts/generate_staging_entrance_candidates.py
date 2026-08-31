"""후보 순서에 등장한 장소를 출입구 수동 검증 대기열로 만든다."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from jeju_trip.application.staging_entrance_candidates import (
    StagingEntrancePlace,
    build_staging_entrance_candidates,
)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"STAGING_ENV_MISSING:{name}")
    return value


def _place_ids(preview: dict[str, Any]) -> tuple[str, ...]:
    values = {str(preview["accommodation"]["place_fact_id"])}
    values.update(
        str(place["place_fact_id"])
        for candidate in preview["candidate_orders"]
        for place in candidate["places"]
    )
    return tuple(sorted(values))


def _places(fact_ids: tuple[str, ...]) -> tuple[StagingEntrancePlace, ...]:
    with psycopg.connect(_required("JEJU_RUNTIME_DSN")) as connection:
        rows = connection.execute(
            """SELECT fact_id, name, category FROM travel_read.active_place
               WHERE fact_id = ANY(%s) ORDER BY name""",
            (list(fact_ids),),
        ).fetchall()
    if len(rows) != len(fact_ids):
        raise ValueError("STAGING_ENTRANCE_ACTIVE_PLACE_MISSING")
    return tuple(StagingEntrancePlace(str(row[0]), str(row[1]), str(row[2])) for row in rows)


def _markdown(payload: dict[str, Any]) -> str:
    items = "\n".join(
        f"- {item['name']} (`{item['place_fact_id']}`): `UNVERIFIED`, 경로 사용 불가"
        for item in payload["items"]
    )
    return f"""# 제주 동부 출입구 검증 후보

생성시각: {payload["generated_at"]}

- 상태: `{payload["status"]}`
- 대상 장소: {len(payload["items"])}곳
- 운영 활성화 허용: `false`
- 외부 API 호출: 0건

## 검증 대기열

{items}

## 다음 확인

각 장소에서 공식 또는 수동 검증된 보행 출입구와 차량 승하차 지점을 별도로 확인해야 한다.
TourAPI 중심좌표는 후보 탐색 기준일 뿐 입구 좌표로 저장하거나 TMAP endpoint로 사용하지 않는다.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-preview", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    preview = json.loads(arguments.candidate_preview.read_text(encoding="utf-8"))
    result = build_staging_entrance_candidates(_places(_place_ids(preview)))
    payload = {**result.as_dict(), "generated_at": datetime.now(UTC).isoformat()}
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    (arguments.output_dir / "entrance-candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (arguments.output_dir / "entrance-candidates.md").write_text(
        _markdown(payload), encoding="utf-8"
    )
    print(
        f"상태: {payload['status']}\n대상 장소: {len(payload['items'])}\n"
        "외부 API 호출: 0\n운영 활성화: false"
    )


if __name__ == "__main__":
    main()
