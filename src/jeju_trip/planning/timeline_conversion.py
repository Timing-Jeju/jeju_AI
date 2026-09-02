"""생성 추천을 손실 없는 v0.7 full-timeline 판정 입력으로 변환한다."""

from __future__ import annotations

from jeju_trip.domain.models import (
    FullTimelineActivity,
    FullTimelineBuffer,
    FullTimelineEvaluationInput,
    FullTimelineTransfer,
    PlaceReference,
    Recommendation,
    RecommendDayTripsInput,
    TimelineEvent,
)


def _neighbor_place_id(
    timeline: tuple[TimelineEvent, ...], index: int, direction: int, hotel_id: str
) -> str:
    cursor = index + direction
    while 0 <= cursor < len(timeline):
        event = timeline[cursor]
        if event.place_id:
            return event.place_id
        if event.visit is not None:
            return event.visit.place_id
        if event.meal is not None:
            return event.meal.place_id
        if event.rest is not None:
            return event.rest.place_id
        cursor += direction
    return hotel_id


def recommendation_to_full_timeline(
    request: RecommendDayTripsInput,
    recommendation: Recommendation,
) -> FullTimelineEvaluationInput:
    """visit·meal·rest·transfer·buffer 및 버스 claim을 모두 보존한다."""

    start_place_id = request.start_boundary.place_id
    end_place_id = request.end_boundary.place_id
    if start_place_id is None or end_place_id is None:
        raise ValueError("DAY_BOUNDARY_UNRESOLVED")
    converted = []
    for index, event in enumerate(recommendation.timeline):
        if event.type == "transfer":
            if event.transfer is None:
                raise ValueError("TRANSFER_DETAILS_MISSING")
            from_id = _neighbor_place_id(
                recommendation.timeline, index, -1, start_place_id
            )
            to_id = _neighbor_place_id(
                recommendation.timeline, index, 1, end_place_id
            )
            first_ride = event.transfer.bus_rides[0] if event.transfer.bus_rides else None
            last_ride = event.transfer.bus_rides[-1] if event.transfer.bus_rides else None
            converted.append(
                FullTimelineTransfer(
                    event_id=event.event_id,
                    type="transfer",
                    start_at=event.start_at,
                    end_at=event.end_at,
                    from_place=PlaceReference(place_id=from_id),
                    to_place=PlaceReference(place_id=to_id),
                    planned_mode=event.transfer.mode,
                    planned_route_id=first_ride.route_id if first_ride else None,
                    planned_route_number=first_ride.route_number if first_ride else None,
                    planned_boarding_stop_id=(
                        first_ride.canonical_boarding_stop_id if first_ride else None
                    ),
                    planned_alighting_stop_id=(
                        last_ride.canonical_alighting_stop_id if last_ride else None
                    ),
                    scheduled_departure_at=(
                        first_ride.scheduled_departure_at if first_ride else None
                    ),
                    scheduled_arrival_at=(last_ride.scheduled_arrival_at if last_ride else None),
                    planned_distance_meters=event.transfer.distance_meters,
                    route_alternatives=event.transfer.alternatives,
                    mode_decision=event.transfer.mode_decision,
                    selected_transfer=event.transfer,
                    evidence_fact_ids=event.evidence_fact_ids,
                )
            )
        elif event.type == "buffer":
            converted.append(
                FullTimelineBuffer(
                    event_id=event.event_id,
                    type="buffer",
                    start_at=event.start_at,
                    end_at=event.end_at,
                    place=PlaceReference(place_id=event.place_id) if event.place_id else None,
                    reason_code=event.reason_code or "PLANNED_SAFETY_BUFFER",
                    evidence_fact_ids=event.evidence_fact_ids,
                )
            )
        else:
            place_id = (
                event.visit.place_id
                if event.visit is not None
                else event.meal.place_id
                if event.meal is not None
                else event.rest.place_id
                if event.rest is not None
                else event.place_id
            )
            if place_id is None:
                raise ValueError("ACTIVITY_PLACE_UNRESOLVED")
            converted.append(
                FullTimelineActivity(
                    event_id=event.event_id,
                    type=event.type,
                    start_at=event.start_at,
                    end_at=event.end_at,
                    place=PlaceReference(place_id=place_id),
                    required=place_id in recommendation.required_place_ids_included,
                )
            )
    return FullTimelineEvaluationInput(
        trip_date=request.trip_date,
        timezone=request.timezone,
        accommodation=request.accommodation,
        activity_window=request.activity_window,
        day_boundary=request.day_boundary,
        place_duration_preferences=request.place_duration_preferences,
        party=request.party,
        transport=request.transport,
        walking=request.walking,
        rest=request.rest,
        food=request.food,
        total_budget_krw=request.total_budget_krw,
        schedule_format="full_timeline",
        timeline=tuple(converted),
    )
