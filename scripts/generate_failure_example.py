"""현재 기본 readiness의 정직한 구조화 실패 예시를 생성한다."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jeju_trip.application.service import TripPlannerService
from jeju_trip.domain.models import (
    AccommodationInput,
    ActivityWindow,
    RecommendDayTripsInput,
)

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))


def main() -> None:
    request = RecommendDayTripsInput(
        trip_date=date(2026, 8, 15),
        accommodation=AccommodationInput(name="제주국제공항"),
        activity_window=ActivityWindow(
            start_at=datetime(2026, 8, 15, 9, tzinfo=KST),
            end_at=datetime(2026, 8, 15, 20, tzinfo=KST),
        ),
        original_text="제주 동부권을 버스로 여행하는 하루 일정을 추천해줘.",
    )
    response = TripPlannerService().recommend(request)
    target = ROOT / "docs" / "examples" / "day-trip-recommendations.example.json"
    target.write_text(
        json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
