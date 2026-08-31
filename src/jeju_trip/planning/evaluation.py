"""생성·정확 일정·실시간 기능이 공유하는 결정론적 일정 판정 엔진."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol, cast

from jeju_trip.domain.models import (
    ActivitiesOnlyEvaluationInput,
    ActivityEvaluation,
    CostRange,
    EvaluateJejuDayTripInput,
    EvaluationIssue,
    EvaluationResponse,
    EvaluationTotals,
    Failure,
    FullTimelineActivity,
    FullTimelineBuffer,
    FullTimelineEvaluationInput,
    FullTimelineTransfer,
    NormalizedSchedule,
    NormalizedScheduleEvent,
    RepairChange,
    RepairOption,
    ScheduledActivity,
    SegmentEvaluation,
    ValidationSummary,
)
from jeju_trip.planning.execution_budget import ExecutionBudget


@dataclass(frozen=True)
class RouteEvidence:
    """외부 adapter가 검증한 문-to-문 이동 사실."""

    mode: Literal["walk", "bus", "taxi"]
    duration_minutes: int
    distance_meters: int | None
    cost_krw: int
    walking_minutes: int
    transfers: int
    evidence_fact_ids: tuple[str, ...]
    walking_distance_meters: int = 0
    route_number: str | None = None
    provider_route_id: str | None = None
    boarding_stop_id: str | None = None
    alighting_stop_id: str | None = None
    scheduled_departure_at: datetime | None = None
    scheduled_arrival_at: datetime | None = None
    scheduled_wait_minutes: int = 0
    stairs_status: Literal["CLEAR", "PRESENT", "UNKNOWN"] = "UNKNOWN"
    cost_min_krw: int | None = None
    cost_max_krw: int | None = None

    @property
    def cost_range(self) -> CostRange:
        """기존 단일 비용을 범위의 양 끝으로만 호환한다."""

        minimum = self.cost_krw if self.cost_min_krw is None else self.cost_min_krw
        maximum = self.cost_krw if self.cost_max_krw is None else self.cost_max_krw
        return CostRange(
            min_krw=minimum,
            max_krw=maximum,
            is_estimated=self.mode == "taxi" or minimum != maximum,
        )


class EvaluationEvidence(Protocol):
    """승인된 publication 또는 일시적 route adapter의 판정용 조회 경계."""

    def route(
        self, from_place_id: str, to_place_id: str, departure_at: datetime
    ) -> RouteEvidence | None: ...

    def opening_window(
        self, place_id: str, on_date: date
    ) -> tuple[datetime, datetime, tuple[str, ...]] | None: ...


def _place_id(place_id: str | None) -> str | None:
    return place_id if place_id and place_id.strip() else None


def _risk_for_slack(slack: int) -> Literal["critical", "high", "medium", "low"]:
    if slack < 0:
        return "critical"
    if slack < 10:
        return "high"
    if slack < 20:
        return "medium"
    return "low"


class ItineraryEvaluationEngine:
    """입력 주장과 공식 근거를 분리해 일정 실행 가능성을 판정한다."""

    def __init__(self, evidence: EvaluationEvidence) -> None:
        self._evidence = evidence

    @staticmethod
    def _with_repairs(
        response: EvaluationResponse, repairs: tuple[RepairOption, ...]
    ) -> EvaluationResponse:
        """model_copy의 무검증 갱신을 피하고 최종 공개 응답 계약을 다시 적용한다."""

        payload = response.model_dump(mode="python")
        payload["repair_options"] = repairs
        return EvaluationResponse.model_validate(payload)

    def evaluate(
        self, request: EvaluateJejuDayTripInput, budget: ExecutionBudget | None = None
    ) -> EvaluationResponse:
        if isinstance(request, ActivitiesOnlyEvaluationInput):
            return self._evaluate_activities(request, budget=budget)
        return self._evaluate_full_timeline(request, budget=budget)

    def _evaluate_activities(
        self,
        request: ActivitiesOnlyEvaluationInput,
        *,
        include_repairs: bool = True,
        budget: ExecutionBudget | None = None,
    ) -> EvaluationResponse:
        activities = sorted(request.scheduled_activities, key=lambda item: item.start_at)
        issues: list[EvaluationIssue] = []
        normalized: list[NormalizedScheduleEvent] = []
        segments: list[SegmentEvaluation] = []
        hotel_id = _place_id(request.accommodation.place_id)
        start_id = (
            _place_id(request.start_location.place_id)
            if request.start_location is not None
            else hotel_id
        )

        if not activities:
            issues.append(
                self._issue(
                    "timeline",
                    "critical",
                    "EMPTY_SCHEDULE",
                    "판정할 활동이 없습니다.",
                )
            )

        for previous, current in zip(activities, activities[1:], strict=False):
            if previous.end_at > current.start_at:
                issues.append(
                    self._issue(
                        "timeline",
                        "critical",
                        "EVENT_OVERLAP",
                        "일정 이벤트가 서로 겹칩니다.",
                        (previous.event_id, current.event_id),
                    )
                )

        endpoints: list[
            tuple[str | None, str | None, datetime, datetime, str | None, str | None]
        ] = []
        if activities:
            endpoints.append(
                (
                    start_id,
                    _place_id(activities[0].place.place_id),
                    request.activity_window.start_at,
                    activities[0].start_at,
                    None,
                    activities[0].event_id,
                )
            )
            for previous, current in zip(activities, activities[1:], strict=False):
                endpoints.append(
                    (
                        _place_id(previous.place.place_id),
                        _place_id(current.place.place_id),
                        previous.end_at,
                        current.start_at,
                        previous.event_id,
                        current.event_id,
                    )
                )
            endpoints.append(
                (
                    _place_id(activities[-1].place.place_id),
                    hotel_id,
                    activities[-1].end_at,
                    request.activity_window.end_at,
                    activities[-1].event_id,
                    None,
                )
            )

        for index, endpoint in enumerate(endpoints, start=1):
            from_id, to_id, available_start, available_end, from_event, to_event = endpoint
            segment, issue, event = self._evaluate_leg(
                request,
                index,
                from_id,
                to_id,
                available_start,
                available_end,
                from_event,
                to_event,
                budget,
            )
            segments.append(segment)
            if issue:
                issues.append(issue)
            normalized.append(event)
            if to_event is not None:
                activity = next(item for item in activities if item.event_id == to_event)
                normalized.append(
                    NormalizedScheduleEvent(
                        event_id=activity.event_id,
                        type=activity.type,
                        start_at=activity.start_at,
                        end_at=activity.end_at,
                        place_id=_place_id(activity.place.place_id),
                        source_event_id=activity.event_id,
                    )
                )
                self._check_opening(request, activity, issues)
                self._check_dietary(request, activity, issues)

        response = self._response(normalized, segments, issues, request)
        if include_repairs and response.status == "infeasible":
            return self._attach_verified_repairs(request, response, budget)
        return response

    def _evaluate_leg(
        self,
        request: ActivitiesOnlyEvaluationInput,
        index: int,
        from_id: str | None,
        to_id: str | None,
        available_start: datetime,
        available_end: datetime,
        from_event: str | None,
        to_event: str | None,
        budget: ExecutionBudget | None,
    ) -> tuple[SegmentEvaluation, EvaluationIssue | None, NormalizedScheduleEvent]:
        available = int((available_end - available_start).total_seconds() // 60)
        locked = next(
            (
                constraint.locked_mode
                for constraint in request.leg_constraints
                if constraint.from_event_id == from_event and constraint.to_event_id == to_event
            ),
            None,
        )
        same_place = from_id is not None and from_id == to_id
        evidence = (
            RouteEvidence(
                mode="walk",
                duration_minutes=0,
                distance_meters=0,
                cost_krw=0,
                walking_minutes=0,
                transfers=0,
                evidence_fact_ids=(),
                stairs_status="CLEAR",
            )
            if same_place
            else self._route(
                from_id,
                to_id,
                available_start,
                budget,
                cast(Literal["walk", "bus", "taxi"] | None, locked),
            )
            if from_id is not None and to_id is not None
            else None
        )
        if evidence is None:
            issue = self._issue(
                "transit_connection",
                "unknown",
                "ROUTE_EVIDENCE_MISSING",
                "검증된 문-to-문 이동 근거가 없습니다.",
                tuple(item for item in (from_event, to_event) if item),
            )
            segment = SegmentEvaluation(
                segment_id=f"segment-{index}",
                from_event_id=from_event,
                to_event_id=to_event,
                mode=None,
                status="unverifiable",
                risk="unknown",
                available_minutes=available,
                required_minutes=None,
                slack_minutes=None,
            )
            duration = max(0, available)
            event = NormalizedScheduleEvent(
                event_id=f"transfer-{index}",
                type="transfer",
                start_at=available_start,
                end_at=available_start + timedelta(minutes=duration),
                from_place_id=from_id,
                to_place_id=to_id,
            )
            return segment, issue, event

        mismatch = not same_place and locked is not None and locked != evidence.mode
        mode_not_allowed = (
            not same_place and evidence.mode not in request.transport.allowed_modes
        )
        transfers_exceeded = evidence.transfers > request.transport.max_transfers_per_leg
        single_walk_exceeded = (
            evidence.mode == "walk"
            and evidence.duration_minutes > request.walking.max_single_leg_minutes
        )
        access_walk_exceeded = (
            evidence.mode == "bus"
            and evidence.walking_minutes
            > request.walking.max_access_walk_minutes * (2 + evidence.transfers)
        )
        stairs_unverifiable = (
            request.walking.avoid_stairs_required and evidence.stairs_status == "UNKNOWN"
        )
        slack = available - evidence.duration_minutes
        risk = (
            "low" if same_place else _risk_for_slack(slack)
        )
        hard_violation = any(
            (
                mismatch,
                mode_not_allowed,
                transfers_exceeded,
                single_walk_exceeded,
                access_walk_exceeded,
            )
        )
        status: Literal["feasible", "infeasible", "unverifiable"] = (
            "unverifiable"
            if stairs_unverifiable
            else "infeasible"
            if slack < 0 or hard_violation
            else "feasible"
        )
        issue = None
        if mismatch:
            issue = self._issue(
                "transit_connection",
                "critical",
                "LOCKED_MODE_UNAVAILABLE",
                "사용자가 고정한 이동수단과 검증된 경로가 일치하지 않습니다.",
                tuple(item for item in (from_event, to_event) if item),
                evidence.evidence_fact_ids,
            )
        elif mode_not_allowed:
            issue = self._issue(
                "transit_connection",
                "critical",
                "MODE_NOT_ALLOWED",
                "검증된 경로가 사용자의 허용 이동수단을 위반합니다.",
                tuple(item for item in (from_event, to_event) if item),
                evidence.evidence_fact_ids,
            )
        elif transfers_exceeded:
            issue = self._issue(
                "transit_connection",
                "critical",
                "TRANSFER_LIMIT_EXCEEDED",
                "검증된 경로의 환승 횟수가 사용자 한도를 넘습니다.",
                tuple(item for item in (from_event, to_event) if item),
                evidence.evidence_fact_ids,
            )
        elif single_walk_exceeded or access_walk_exceeded:
            issue = self._issue(
                "walking",
                "critical",
                "WALKING_LIMIT_EXCEEDED",
                "검증된 경로의 도보 시간이 사용자 한도를 넘습니다.",
                tuple(item for item in (from_event, to_event) if item),
                evidence.evidence_fact_ids,
            )
        elif stairs_unverifiable:
            issue = self._issue(
                "accessibility",
                "unknown",
                "ACCESSIBILITY_UNVERIFIABLE",
                "경로 API가 계단 여부를 제공하지 않아 안전을 확인할 수 없습니다.",
                tuple(item for item in (from_event, to_event) if item),
                evidence.evidence_fact_ids,
            )
        elif slack < 0:
            issue = self._issue(
                "transit_connection",
                "critical",
                "NEGATIVE_TRANSFER_SLACK",
                "공식 이동시간보다 활동 사이 이동 여유가 짧습니다.",
                tuple(item for item in (from_event, to_event) if item),
                evidence.evidence_fact_ids,
                slack,
            )
        segment = SegmentEvaluation(
            segment_id=f"segment-{index}",
            from_event_id=from_event,
            to_event_id=to_event,
            mode=None if same_place else evidence.mode,
            status=status,
            risk="unknown" if stairs_unverifiable else "critical" if hard_violation else risk,
            available_minutes=available,
            required_minutes=evidence.duration_minutes,
            slack_minutes=slack,
            evidence_fact_ids=evidence.evidence_fact_ids,
            walking_minutes=evidence.walking_minutes,
            walking_distance_meters=(
                evidence.walking_distance_meters
                or (
                    evidence.distance_meters
                    if evidence.mode == "walk" and evidence.distance_meters is not None
                    else 0
                )
            ),
            distance_meters=evidence.distance_meters,
            cost_krw=evidence.cost_range.max_krw,
            cost=evidence.cost_range,
            bus_wait_minutes=(evidence.scheduled_wait_minutes if evidence.mode == "bus" else 0),
            transfers=evidence.transfers,
            reason_codes=(("ALREADY_AT_DESTINATION",) if same_place else ()),
        )
        event = NormalizedScheduleEvent(
            event_id=f"transfer-{index}",
            type="transfer",
            start_at=available_start,
            end_at=available_start + timedelta(minutes=evidence.duration_minutes),
            from_place_id=from_id,
            to_place_id=to_id,
            mode=None if same_place else evidence.mode,
        )
        return segment, issue, event

    def _check_opening(self, request, activity, issues: list[EvaluationIssue]) -> None:
        place_id = _place_id(activity.place.place_id)
        windows_reader = getattr(self._evidence, "opening_windows", None)
        if place_id is None:
            openings = ()
        elif windows_reader is not None:
            openings = windows_reader(place_id, request.trip_date)
        else:
            opening = self._evidence.opening_window(place_id, request.trip_date)
            openings = (opening,) if opening is not None else ()
        if not openings:
            issues.append(
                self._issue(
                    "opening_hours",
                    "unknown",
                    "OPENING_HOURS_UNKNOWN",
                    "운영시간 근거가 없어 활동 가능 여부를 확정할 수 없습니다.",
                    (activity.event_id,),
                )
            )
            return
        if not any(
            activity.start_at >= opens_at and activity.end_at <= closes_at
            for opens_at, closes_at, _ in openings
        ):
            fact_ids = tuple(
                dict.fromkeys(
                    fact_id for _, _, window_fact_ids in openings for fact_id in window_fact_ids
                )
            )
            issues.append(
                self._issue(
                    "opening_hours",
                    "critical",
                    "PLACE_CLOSED",
                    "계획된 활동 시각이 검증된 운영시간 밖입니다.",
                    (activity.event_id,),
                    fact_ids,
                )
            )
            return
        last_admission_reader = getattr(self._evidence, "last_admission_at", None)
        last_admission = (
            last_admission_reader(place_id, request.trip_date)
            if last_admission_reader is not None and place_id is not None
            else None
        )
        if last_admission is not None and activity.start_at > last_admission[0]:
            issues.append(
                self._issue(
                    "opening_hours",
                    "critical",
                    "LAST_ADMISSION_MISSED",
                    "계획된 입장 시각이 검증된 마지막 입장 시각보다 늦습니다.",
                    (activity.event_id,),
                    last_admission[1],
                )
            )

    def _check_dietary(self, request, activity, issues: list[EvaluationIssue]) -> None:
        if activity.type != "meal" or not (
            request.food.allergens or request.food.excluded_foods
        ):
            return
        place_id = _place_id(activity.place.place_id)
        checker = getattr(self._evidence, "dietary_safety", None)
        result = (
            checker(
                place_id,
                request.food.allergens,
                request.food.excluded_foods,
                request.trip_date,
            )
            if checker and place_id
            else None
        )
        if result is None:
            issues.append(
                self._issue(
                    "meal_rest",
                    "unknown",
                    "DIETARY_SAFETY_UNVERIFIABLE",
                    "알레르기·제외음식 안전성을 확인할 공식 근거가 없습니다.",
                    (activity.event_id,),
                )
            )
        elif not result[0]:
            issues.append(
                self._issue(
                    "meal_rest",
                    "critical",
                    "DIETARY_CONSTRAINT_VIOLATED",
                    "검증된 음식 정보가 사용자 알레르기·제외음식 조건을 위반합니다.",
                    (activity.event_id,),
                    result[1],
                )
            )

    def _evaluate_full_timeline(
        self,
        request: FullTimelineEvaluationInput,
        *,
        include_repairs: bool = True,
        budget: ExecutionBudget | None = None,
    ) -> EvaluationResponse:
        ordered = sorted(request.timeline, key=lambda item: item.start_at)
        normalized: list[NormalizedScheduleEvent] = []
        issues: list[EvaluationIssue] = []
        segments: list[SegmentEvaluation] = []
        hotel_id = _place_id(request.accommodation.place_id)
        transfer_items = [item for item in ordered if item.type == "transfer"]
        if not ordered:
            issues.append(
                self._issue(
                    "timeline",
                    "critical",
                    "EMPTY_SCHEDULE",
                    "판정할 전체 타임라인이 없습니다.",
                )
            )
        elif (
            hotel_id is None
            or not transfer_items
            or _place_id(transfer_items[0].from_place.place_id) != hotel_id
            or ordered[0].start_at != request.activity_window.start_at
            or _place_id(transfer_items[-1].to_place.place_id) != hotel_id
            or ordered[-1].end_at > request.activity_window.end_at
        ):
            issues.append(
                self._issue(
                    "hotel_return",
                    "critical",
                    "TIMELINE_BOUNDARY_INCOMPLETE",
                    "전체 타임라인은 숙소 출발부터 숙소 복귀까지 완전해야 합니다.",
                    tuple(item.event_id for item in ordered[:1] + ordered[-1:]),
                )
            )
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.end_at > current.start_at:
                issues.append(
                    self._issue(
                        "timeline",
                        "critical",
                        "EVENT_OVERLAP",
                        "전체 타임라인의 이벤트가 겹칩니다.",
                        (previous.event_id, current.event_id),
                    )
                )
            elif previous.end_at < current.start_at:
                issues.append(
                    self._issue(
                        "timeline",
                        "critical",
                        "EVENT_GAP",
                        "전체 타임라인의 인접 이벤트 사이가 비어 있습니다.",
                        (previous.event_id, current.event_id),
                    )
                )
                normalized.append(
                    NormalizedScheduleEvent(
                        event_id=f"gap-{previous.event_id}-{current.event_id}",
                        type="unplanned_gap",
                        start_at=previous.end_at,
                        end_at=current.start_at,
                    )
                )
            previous_place = (
                _place_id(previous.to_place.place_id)
                if previous.type == "transfer"
                else _place_id(previous.place.place_id)
                if previous.place is not None
                else None
            )
            current_place = (
                _place_id(current.from_place.place_id)
                if current.type == "transfer"
                else _place_id(current.place.place_id)
                if current.place is not None
                else previous_place
            )
            if previous_place is None or current_place is None:
                issues.append(
                    self._issue(
                        "timeline",
                        "unknown",
                        "PLACE_POSITION_UNRESOLVED",
                        "연속 위치를 검증할 장소 ID가 없습니다.",
                        (previous.event_id, current.event_id),
                    )
                )
            elif previous_place != current_place:
                issues.append(
                    self._issue(
                        "timeline",
                        "critical",
                        "LOCATION_DISCONTINUITY",
                        "인접 이벤트 사이의 위치가 연속되지 않습니다.",
                        (previous.event_id, current.event_id),
                    )
                )
        for item_index, item in enumerate(ordered):
            if item.type == "transfer":
                if item.planned_mode == "taxi":
                    preceding = ordered[item_index - 1] if item_index else None
                    expected_pickup = (
                        item.mode_decision.taxi_pickup_buffer_minutes
                        if item.mode_decision is not None
                        and item.mode_decision.taxi_pickup_buffer_minutes is not None
                        else request.transport.taxi_pickup_buffer_minutes
                    )
                    if (
                        preceding is None
                        or preceding.type != "buffer"
                        or preceding.reason_code != "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
                        or preceding.end_at != item.start_at
                    ):
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "critical",
                                "TAXI_PICKUP_BUFFER_MISSING",
                                "택시 주행 직전에 정책 호출 버퍼가 필요합니다.",
                                (item.event_id,),
                            )
                        )
                    elif (
                        int((preceding.end_at - preceding.start_at).total_seconds() // 60)
                        != expected_pickup
                    ):
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "critical",
                                "TAXI_PICKUP_BUFFER_MISMATCH",
                                "택시 호출 버퍼가 수단 선택 정책과 일치하지 않습니다.",
                                (preceding.event_id, item.event_id),
                            )
                        )
                from_id = _place_id(item.from_place.place_id)
                to_id = _place_id(item.to_place.place_id)
                planned_minutes = int((item.end_at - item.start_at).total_seconds() // 60)
                route = (
                    self._route(
                        from_id,
                        to_id,
                        item.start_at,
                        budget,
                        item.planned_mode,
                    )
                    if from_id is not None and to_id is not None
                    else None
                )
                if route is None:
                    segments.append(
                        SegmentEvaluation(
                            segment_id=item.event_id,
                            from_event_id=None,
                            to_event_id=None,
                            mode=item.planned_mode,
                            status="unverifiable",
                            risk="unknown",
                            available_minutes=planned_minutes,
                            required_minutes=None,
                            slack_minutes=None,
                        )
                    )
                    issues.append(
                        self._issue(
                            "transit_connection",
                            "unknown",
                            "ROUTE_EVIDENCE_MISSING",
                            "사용자 이동 주장을 대조할 공식 경로 근거가 없습니다.",
                            (item.event_id,),
                        )
                    )
                else:
                    safety_buffer_minutes = 0
                    cursor = item_index + 1
                    while cursor < len(ordered) and ordered[cursor].type == "buffer":
                        safety_buffer_minutes += int(
                            (ordered[cursor].end_at - ordered[cursor].start_at).total_seconds()
                            // 60
                        )
                        cursor += 1
                    if item_index == len(ordered) - 1:
                        safety_buffer_minutes += max(
                            0,
                            int(
                                (request.activity_window.end_at - item.end_at).total_seconds() // 60
                            ),
                        )
                    slack = planned_minutes + safety_buffer_minutes - route.duration_minutes
                    distance_unverifiable = (
                        item.planned_distance_meters is not None and route.distance_meters is None
                    )
                    bus_claim_pairs = (
                        (item.planned_route_id, route.provider_route_id),
                        (item.planned_route_number, route.route_number),
                        (item.planned_boarding_stop_id, route.boarding_stop_id),
                        (item.planned_alighting_stop_id, route.alighting_stop_id),
                        (item.scheduled_departure_at, route.scheduled_departure_at),
                        (item.scheduled_arrival_at, route.scheduled_arrival_at),
                    )
                    bus_claim_unverifiable = item.planned_mode == "bus" and any(
                        official is None for _, official in bus_claim_pairs
                    )
                    distance_changed = (
                        item.planned_distance_meters is not None
                        and route.distance_meters is not None
                        and abs(item.planned_distance_meters - route.distance_meters)
                        > max(500, int(item.planned_distance_meters * 0.1))
                    )
                    claim_mismatch = item.planned_mode != route.mode or any(
                        planned != official for planned, official in bus_claim_pairs
                    )
                    mode_not_allowed = item.planned_mode not in request.transport.allowed_modes
                    transfers_exceeded = route.transfers > request.transport.max_transfers_per_leg
                    walking_exceeded = (
                        route.duration_minutes > request.walking.max_single_leg_minutes
                        if route.mode == "walk"
                        else route.walking_minutes
                        > request.walking.max_access_walk_minutes * (2 + route.transfers)
                        if route.mode == "bus"
                        else False
                    )
                    stairs_unverifiable = (
                        request.walking.avoid_stairs_required and route.stairs_status == "UNKNOWN"
                    )
                    hard_violation = any(
                        (
                            claim_mismatch,
                            mode_not_allowed,
                            transfers_exceeded,
                            walking_exceeded,
                            slack < 0,
                        )
                    )
                    if distance_changed:
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "low",
                                "ROUTE_SNAPSHOT_CHANGED",
                                "경로 스냅샷 거리가 허용 범위보다 달라졌지만 "
                                "시간 판정은 최신 소요시간을 사용합니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                                slack,
                            )
                        )
                    if distance_unverifiable or bus_claim_unverifiable:
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "unknown",
                                "USER_ROUTE_FACT_UNVERIFIABLE",
                                "공식 경로에 총 이동거리가 없어 "
                                "입력한 거리 주장을 검증할 수 없습니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                                slack,
                            )
                        )
                    elif claim_mismatch:
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "critical",
                                "USER_ROUTE_FACT_MISMATCH",
                                "입력한 이동정보가 공식 경로 사실과 일치하지 않습니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                                slack,
                            )
                        )
                    elif mode_not_allowed:
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "critical",
                                "MODE_NOT_ALLOWED",
                                "전체 일정의 이동수단이 사용자 허용 범위를 벗어납니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                            )
                        )
                    elif transfers_exceeded:
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "critical",
                                "TRANSFER_LIMIT_EXCEEDED",
                                "전체 일정의 환승 횟수가 사용자 한도를 넘습니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                            )
                        )
                    elif walking_exceeded:
                        issues.append(
                            self._issue(
                                "walking",
                                "critical",
                                "WALKING_LIMIT_EXCEEDED",
                                "전체 일정의 구간 도보가 사용자 한도를 넘습니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                            )
                        )
                    elif stairs_unverifiable:
                        issues.append(
                            self._issue(
                                "accessibility",
                                "unknown",
                                "ACCESSIBILITY_UNVERIFIABLE",
                                "계단 회피 필수 조건을 공식 경로로 검증할 수 없습니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                            )
                        )
                    elif slack < 0:
                        issues.append(
                            self._issue(
                                "transit_connection",
                                "critical",
                                "NEGATIVE_TRANSFER_SLACK",
                                "계획한 이동시간이 공식 문-to-문 소요시간보다 짧습니다.",
                                (item.event_id,),
                                route.evidence_fact_ids,
                                slack,
                            )
                        )
                    segments.append(
                        SegmentEvaluation(
                            segment_id=item.event_id,
                            from_event_id=None,
                            to_event_id=None,
                            mode=item.planned_mode,
                            status=(
                                "unverifiable"
                                if stairs_unverifiable
                                or distance_unverifiable
                                or bus_claim_unverifiable
                                else "infeasible"
                                if hard_violation
                                else "feasible"
                            ),
                            risk=(
                                "unknown"
                                if stairs_unverifiable
                                or distance_unverifiable
                                or bus_claim_unverifiable
                                else "critical"
                                if hard_violation
                                else _risk_for_slack(slack)
                            ),
                            available_minutes=planned_minutes + safety_buffer_minutes,
                            required_minutes=route.duration_minutes,
                            slack_minutes=slack,
                            walking_minutes=route.walking_minutes,
                            walking_distance_meters=(
                                route.walking_distance_meters
                                or (
                                    route.distance_meters
                                    if route.mode == "walk" and route.distance_meters is not None
                                    else 0
                                )
                            ),
                            distance_meters=route.distance_meters,
                            cost_krw=route.cost_range.max_krw,
                            cost=route.cost_range,
                            bus_wait_minutes=(
                                route.scheduled_wait_minutes if route.mode == "bus" else 0
                            ),
                            transfers=route.transfers,
                            evidence_fact_ids=route.evidence_fact_ids,
                        )
                    )
                normalized.append(
                    NormalizedScheduleEvent(
                        event_id=item.event_id,
                        type="transfer",
                        start_at=item.start_at,
                        end_at=item.end_at,
                        from_place_id=from_id,
                        to_place_id=to_id,
                        mode=item.planned_mode,
                        source_event_id=item.event_id,
                    )
                )
            elif item.type == "buffer":
                normalized.append(
                    NormalizedScheduleEvent(
                        event_id=item.event_id,
                        type="buffer",
                        start_at=item.start_at,
                        end_at=item.end_at,
                        place_id=(
                            _place_id(item.place.place_id) if item.place is not None else None
                        ),
                        source_event_id=item.event_id,
                    )
                )
            else:
                normalized.append(
                    NormalizedScheduleEvent(
                        event_id=item.event_id,
                        type=item.type,
                        start_at=item.start_at,
                        end_at=item.end_at,
                        place_id=_place_id(item.place.place_id),
                        source_event_id=item.event_id,
                    )
                )
                self._check_opening(request, item, issues)
                self._check_dietary(request, item, issues)
                if item.type == "meal" and not (
                    (item.start_at.hour, item.start_at.minute) >= (11, 30)
                    and (item.end_at.hour, item.end_at.minute) <= (14, 0)
                ):
                    issues.append(
                        self._issue(
                            "meal_rest",
                            "critical",
                            "MEAL_WINDOW_MISSED",
                            "식사 활동은 11시 30분부터 14시 사이에 완료되어야 합니다.",
                            (item.event_id,),
                        )
                    )
        normalized.sort(key=lambda item: item.start_at)
        response = self._response(normalized, segments, issues, request)
        if include_repairs and response.status == "infeasible":
            return self._attach_full_timeline_repairs(request, response, budget)
        return response

    def _attach_full_timeline_repairs(
        self,
        request: FullTimelineEvaluationInput,
        response: EvaluationResponse,
        budget: ExecutionBudget | None,
    ) -> EvaluationResponse:
        """원본 복사본에서 단축·건너뛰기·검증된 빠른 수단을 전체 재평가한다."""

        repairs: list[RepairOption] = []
        negative = next(
            (
                segment
                for segment in response.segment_evaluations
                if segment.slack_minutes is not None and segment.slack_minutes < 0
            ),
            None,
        )
        if negative is None:
            return response
        transfer_index = next(
            (
                index
                for index, item in enumerate(request.timeline)
                if isinstance(item, FullTimelineTransfer) and item.event_id == negative.segment_id
            ),
            None,
        )
        if transfer_index is None:
            return response
        transfer = request.timeline[transfer_index]
        if not isinstance(transfer, FullTimelineTransfer):
            return response

        previous = request.timeline[transfer_index - 1] if transfer_index else None
        if (
            isinstance(previous, FullTimelineActivity)
            and not previous.required
            and negative.evidence_fact_ids
        ):
            gained = abs(negative.slack_minutes or 0)
            shortened_end = previous.end_at - timedelta(minutes=gained)
            if shortened_end > previous.start_at:
                repaired_items = list(request.timeline)
                repaired_items[transfer_index - 1] = previous.model_copy(
                    update={"end_at": shortened_end}
                )
                repaired_items[transfer_index] = transfer.model_copy(
                    update={"start_at": shortened_end}
                )
                repaired = self._evaluate_full_timeline(
                    request.model_copy(update={"timeline": tuple(repaired_items)}),
                    include_repairs=False,
                    budget=budget,
                )
                if repaired.status in {"feasible", "feasible_with_caution"}:
                    repairs.append(
                        RepairOption(
                            repair_id=f"repair-full-shorten-{previous.event_id}",
                            repair_type="SHORTEN_STAY",
                            changes=(
                                RepairChange(
                                    event_id=previous.event_id,
                                    field="end_at",
                                    before=previous.end_at,
                                    after=shortened_end,
                                ),
                                RepairChange(
                                    event_id=transfer.event_id,
                                    field="start_at",
                                    before=transfer.start_at,
                                    after=shortened_end,
                                ),
                            ),
                            result_if_applied=cast(
                                Literal["feasible", "feasible_with_caution"], repaired.status
                            ),
                            new_overall_risk=cast(
                                Literal["high", "medium", "low"], repaired.overall_risk
                            ),
                            tradeoffs=("선택 활동 체류시간이 줄어듭니다.",),
                            evidence_fact_ids=negative.evidence_fact_ids,
                            time_gained_minutes=gained,
                            changed_activity_ids=(previous.event_id,),
                            new_evidence_fact_ids=negative.evidence_fact_ids,
                            revalidated_evaluation=repaired,
                        )
                    )

        for alternative in sorted(
            (
                item
                for item in transfer.route_alternatives
                if item.status == "feasible"
                and not item.selected
                and item.duration_minutes is not None
                and item.mode != transfer.planned_mode
            ),
            key=lambda item: (item.duration_minutes or 0, item.mode),
        ):
            from_id = _place_id(transfer.from_place.place_id)
            to_id = _place_id(transfer.to_place.place_id)
            if from_id is None or to_id is None:
                continue
            route = self._route(
                from_id,
                to_id,
                transfer.start_at,
                budget,
                alternative.mode,
            )
            if route is None or route.mode != alternative.mode or not route.evidence_fact_ids:
                continue
            if route.mode == "taxi" and not (
                isinstance(previous, FullTimelineBuffer)
                and previous.reason_code == "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
                and previous.end_at == transfer.start_at
            ):
                continue
            replacement = self._replacement_transfer(transfer, route)
            if replacement is None:
                continue
            repaired_items = list(request.timeline)
            repaired_items[transfer_index] = replacement
            repaired = self._evaluate_full_timeline(
                request.model_copy(update={"timeline": tuple(repaired_items)}),
                include_repairs=False,
                budget=budget,
            )
            if repaired.status not in {"feasible", "feasible_with_caution"}:
                continue
            repairs.append(
                RepairOption(
                    repair_id=f"repair-full-faster-{transfer.event_id}-{route.mode}",
                    repair_type="USE_FASTER_VERIFIED_ALTERNATIVE",
                    changes=(
                        RepairChange(
                            event_id=transfer.event_id,
                            field="planned_mode",
                            before=transfer.planned_mode,
                            after=route.mode,
                        ),
                    ),
                    result_if_applied=cast(
                        Literal["feasible", "feasible_with_caution"], repaired.status
                    ),
                    new_overall_risk=cast(Literal["high", "medium", "low"], repaired.overall_risk),
                    tradeoffs=("검증된 더 빠른 이동수단으로 바뀝니다.",),
                    evidence_fact_ids=route.evidence_fact_ids,
                    time_gained_minutes=max(
                        0, (negative.required_minutes or 0) - route.duration_minutes
                    ),
                    cost_increase=self._cost_increase(negative.cost, route.cost_range),
                    new_evidence_fact_ids=route.evidence_fact_ids,
                    revalidated_evaluation=repaired,
                )
            )
            break

        taxi_alternative = next(
            (
                item
                for item in transfer.route_alternatives
                if transfer.planned_mode == "bus"
                and item.mode == "taxi"
                and item.status == "feasible"
            ),
            None,
        )
        pickup_minutes = request.transport.taxi_pickup_buffer_minutes
        pickup_end = transfer.start_at + timedelta(minutes=pickup_minutes)
        if taxi_alternative is not None and pickup_end < transfer.end_at:
            from_id = _place_id(transfer.from_place.place_id)
            to_id = _place_id(transfer.to_place.place_id)
            route = (
                self._route(from_id, to_id, pickup_end, budget, "taxi")
                if from_id is not None and to_id is not None
                else None
            )
            taxi_claim = (
                transfer.model_copy(update={"start_at": pickup_end}) if route is not None else None
            )
            replacement = (
                self._replacement_transfer(taxi_claim, route)
                if taxi_claim is not None and route is not None and route.mode == "taxi"
                else None
            )
            if replacement is not None and route is not None and route.evidence_fact_ids:
                pickup = FullTimelineBuffer(
                    event_id=f"repair-taxi-pickup-{transfer.event_id}",
                    type="buffer",
                    start_at=transfer.start_at,
                    end_at=pickup_end,
                    place=transfer.from_place,
                    reason_code="TAXI_PICKUP_PLANNING_BUFFER_APPLIED",
                    evidence_fact_ids=route.evidence_fact_ids,
                )
                repaired_items = [
                    *request.timeline[:transfer_index],
                    pickup,
                    replacement,
                    *request.timeline[transfer_index + 1 :],
                ]
                repaired = self._evaluate_full_timeline(
                    request.model_copy(update={"timeline": tuple(repaired_items)}),
                    include_repairs=False,
                    budget=budget,
                )
                if repaired.status in {"feasible", "feasible_with_caution"}:
                    repairs.append(
                        RepairOption(
                            repair_id=f"repair-full-taxi-timeout-{transfer.event_id}",
                            repair_type="TAXI_AFTER_BUS_TIMEOUT",
                            changes=(
                                RepairChange(
                                    event_id=transfer.event_id,
                                    field="planned_mode",
                                    before="bus",
                                    after="taxi",
                                ),
                            ),
                            result_if_applied=cast(
                                Literal["feasible", "feasible_with_caution"], repaired.status
                            ),
                            new_overall_risk=cast(
                                Literal["high", "medium", "low"], repaired.overall_risk
                            ),
                            tradeoffs=("버스 대기 한도 초과 뒤 검증된 택시로 전환합니다.",),
                            evidence_fact_ids=route.evidence_fact_ids,
                            time_gained_minutes=max(
                                0,
                                (negative.required_minutes or 0)
                                - pickup_minutes
                                - route.duration_minutes,
                            ),
                            cost_increase=self._cost_increase(negative.cost, route.cost_range),
                            new_evidence_fact_ids=route.evidence_fact_ids,
                            revalidated_evaluation=repaired,
                        )
                    )

        if isinstance(previous, FullTimelineActivity) and not previous.required:
            inbound_index = next(
                (
                    index
                    for index in range(transfer_index - 2, -1, -1)
                    if isinstance(request.timeline[index], FullTimelineTransfer)
                ),
                None,
            )
            if inbound_index is not None:
                inbound = request.timeline[inbound_index]
                assert isinstance(inbound, FullTimelineTransfer)
                from_id = _place_id(inbound.from_place.place_id)
                to_id = _place_id(transfer.to_place.place_id)
                route = (
                    self._route(from_id, to_id, inbound.start_at, budget)
                    if from_id is not None and to_id is not None
                    else None
                )
                if route is not None and route.mode != "taxi" and route.evidence_fact_ids:
                    bridge = self._replacement_transfer(
                        inbound.model_copy(
                            update={
                                "event_id": f"repair-bridge-{previous.event_id}",
                                "end_at": transfer.end_at,
                                "to_place": transfer.to_place,
                            }
                        ),
                        route,
                    )
                    if bridge is not None:
                        repaired_items = [
                            *request.timeline[:inbound_index],
                            bridge,
                            *request.timeline[transfer_index + 1 :],
                        ]
                        repaired = self._evaluate_full_timeline(
                            request.model_copy(update={"timeline": tuple(repaired_items)}),
                            include_repairs=False,
                            budget=budget,
                        )
                        if repaired.status in {"feasible", "feasible_with_caution"}:
                            old_cost = CostRange(
                                min_krw=sum(
                                    item.cost.min_krw
                                    for item in response.segment_evaluations
                                    if item.segment_id in {inbound.event_id, transfer.event_id}
                                ),
                                max_krw=sum(
                                    item.cost.max_krw
                                    for item in response.segment_evaluations
                                    if item.segment_id in {inbound.event_id, transfer.event_id}
                                ),
                                is_estimated=True,
                            )
                            repairs.append(
                                RepairOption(
                                    repair_id=f"repair-full-skip-{previous.event_id}",
                                    repair_type="SKIP_OPTIONAL_ACTIVITY",
                                    changes=(
                                        RepairChange(
                                            event_id=previous.event_id,
                                            field="timeline",
                                            before="included",
                                            after="removed",
                                        ),
                                    ),
                                    result_if_applied=cast(
                                        Literal["feasible", "feasible_with_caution"],
                                        repaired.status,
                                    ),
                                    new_overall_risk=cast(
                                        Literal["high", "medium", "low"],
                                        repaired.overall_risk,
                                    ),
                                    tradeoffs=("선택 활동 하나를 건너뜁니다.",),
                                    evidence_fact_ids=route.evidence_fact_ids,
                                    time_gained_minutes=int(
                                        (previous.end_at - previous.start_at).total_seconds() // 60
                                    ),
                                    cost_increase=self._cost_increase(old_cost, route.cost_range),
                                    changed_activity_ids=(previous.event_id,),
                                    new_evidence_fact_ids=route.evidence_fact_ids,
                                    revalidated_evaluation=repaired,
                                )
                            )
        return self._with_repairs(response, tuple(repairs[:5]))

    @staticmethod
    def _cost_increase(original: CostRange, replacement: CostRange) -> CostRange:
        return CostRange(
            min_krw=max(0, replacement.min_krw - original.max_krw),
            max_krw=max(0, replacement.max_krw - original.min_krw),
            is_estimated=original.is_estimated or replacement.is_estimated,
        )

    @staticmethod
    def _replacement_transfer(
        original: FullTimelineTransfer, route: RouteEvidence
    ) -> FullTimelineTransfer | None:
        if route.mode == "bus" and any(
            item is None
            for item in (
                route.provider_route_id,
                route.route_number,
                route.boarding_stop_id,
                route.alighting_stop_id,
                route.scheduled_departure_at,
                route.scheduled_arrival_at,
            )
        ):
            return None
        return FullTimelineTransfer(
            event_id=original.event_id,
            type="transfer",
            start_at=original.start_at,
            end_at=original.end_at,
            from_place=original.from_place,
            to_place=original.to_place,
            planned_mode=route.mode,
            planned_route_number=route.route_number if route.mode == "bus" else None,
            planned_route_id=route.provider_route_id if route.mode == "bus" else None,
            planned_boarding_stop_id=route.boarding_stop_id if route.mode == "bus" else None,
            planned_alighting_stop_id=route.alighting_stop_id if route.mode == "bus" else None,
            scheduled_departure_at=route.scheduled_departure_at if route.mode == "bus" else None,
            scheduled_arrival_at=route.scheduled_arrival_at if route.mode == "bus" else None,
            planned_distance_meters=route.distance_meters,
            evidence_fact_ids=route.evidence_fact_ids,
        )

    @staticmethod
    def _issue(
        category,
        severity,
        reason_code: str,
        message: str,
        event_ids: tuple[str, ...] = (),
        evidence_fact_ids: tuple[str, ...] = (),
        slack_minutes: int | None = None,
    ) -> EvaluationIssue:
        return EvaluationIssue(
            issue_id=f"issue-{reason_code.lower()}-{len(event_ids)}",
            category=category,
            severity=severity,
            event_ids=event_ids,
            reason_code=reason_code,
            slack_minutes=slack_minutes,
            message=message,
            evidence_fact_ids=evidence_fact_ids,
        )

    def _attach_verified_repairs(
        self,
        request: ActivitiesOnlyEvaluationInput,
        response: EvaluationResponse,
        budget: ExecutionBudget | None,
    ) -> EvaluationResponse:
        repairs: list[RepairOption] = []
        first_negative = next(
            (
                segment
                for segment in response.segment_evaluations
                if segment.slack_minutes is not None
                and segment.slack_minutes < 0
                and segment.from_event_id is None
                and segment.evidence_fact_ids
            ),
            None,
        )
        if first_negative is not None and first_negative.slack_minutes is not None:
            earlier_start = request.activity_window.start_at + timedelta(
                minutes=first_negative.slack_minutes
            )
            if earlier_start.date() == request.trip_date:
                repaired_window = request.activity_window.model_copy(
                    update={"start_at": earlier_start}
                )
                repaired_request = request.model_copy(update={"activity_window": repaired_window})
                repaired = self._evaluate_activities(
                    repaired_request, include_repairs=False, budget=budget
                )
                risk = repaired.overall_risk
                if repaired.status in {"feasible", "feasible_with_caution"} and risk in {
                    "high",
                    "medium",
                    "low",
                }:
                    repairs.append(
                        RepairOption(
                            repair_id="repair-depart-earlier",
                            repair_type="DEPART_EARLIER",
                            changes=(
                                RepairChange(
                                    event_id="accommodation-departure",
                                    field="start_at",
                                    before=request.activity_window.start_at,
                                    after=earlier_start,
                                ),
                            ),
                            result_if_applied=cast(
                                Literal["feasible", "feasible_with_caution"], repaired.status
                            ),
                            new_overall_risk=cast(Literal["high", "medium", "low"], risk),
                            tradeoffs=("하루 활동 시작 시각이 앞당겨집니다.",),
                            evidence_fact_ids=first_negative.evidence_fact_ids,
                            time_gained_minutes=abs(first_negative.slack_minutes),
                            new_evidence_fact_ids=first_negative.evidence_fact_ids,
                            revalidated_evaluation=repaired,
                        )
                    )
        negative = next(
            (
                segment
                for segment in response.segment_evaluations
                if segment.slack_minutes is not None
                and segment.slack_minutes < 0
                and segment.from_event_id is not None
            ),
            None,
        )
        if negative is None:
            return self._with_repairs(response, tuple(repairs))
        original = next(
            (
                activity
                for activity in request.scheduled_activities
                if activity.event_id == negative.from_event_id
            ),
            None,
        )
        if original is None:
            return self._with_repairs(response, tuple(repairs))
        negative_slack = negative.slack_minutes
        if negative_slack is None:
            return self._with_repairs(response, tuple(repairs))
        shortened_end = original.end_at + timedelta(minutes=negative_slack)
        if shortened_end <= original.start_at:
            return self._with_repairs(response, tuple(repairs))
        shortened = ScheduledActivity.model_validate(
            {**original.model_dump(mode="python"), "end_at": shortened_end}
        )
        repaired_request = request.model_copy(
            update={
                "scheduled_activities": tuple(
                    shortened if item.event_id == shortened.event_id else item
                    for item in request.scheduled_activities
                )
            }
        )
        repaired = self._evaluate_activities(repaired_request, include_repairs=False, budget=budget)
        if repaired.status not in {"feasible", "feasible_with_caution"}:
            return self._with_repairs(response, tuple(repairs))
        fact_ids = negative.evidence_fact_ids
        if not fact_ids:
            return self._with_repairs(response, tuple(repairs))
        risk = repaired.overall_risk
        if risk not in {"high", "medium", "low"}:
            return self._with_repairs(response, tuple(repairs))
        repair = RepairOption(
            repair_id=f"repair-shorten-{original.event_id}",
            repair_type="SHORTEN_STAY",
            changes=(
                RepairChange(
                    event_id=original.event_id,
                    field="end_at",
                    before=original.end_at,
                    after=shortened_end,
                ),
            ),
            result_if_applied=cast(Literal["feasible", "feasible_with_caution"], repaired.status),
            new_overall_risk=cast(Literal["high", "medium", "low"], risk),
            tradeoffs=("해당 활동의 체류시간이 줄어듭니다.",),
            evidence_fact_ids=fact_ids,
            time_gained_minutes=abs(negative_slack),
            changed_activity_ids=(original.event_id,),
            new_evidence_fact_ids=fact_ids,
            revalidated_evaluation=repaired,
        )
        repairs.append(repair)
        return self._with_repairs(response, tuple(repairs[:5]))

    def _route(
        self,
        from_place_id: str,
        to_place_id: str,
        departure_at: datetime,
        budget: ExecutionBudget | None,
        required_mode: Literal["walk", "bus", "taxi"] | None = None,
    ) -> RouteEvidence | None:
        if budget is not None:
            budget.claim_external_call("routing")
        route_for_mode = getattr(self._evidence, "route_for_mode", None)
        if required_mode is not None and route_for_mode is not None:
            return route_for_mode(from_place_id, to_place_id, departure_at, required_mode)
        return self._evidence.route(from_place_id, to_place_id, departure_at)

    def _response(self, normalized, segments, issues, request) -> EvaluationResponse:
        total_walking_distance = sum(segment.walking_distance_meters for segment in segments)
        total_cost_min = sum(segment.cost.min_krw for segment in segments)
        total_cost_max = sum(segment.cost.max_krw for segment in segments)
        segment_fact_ids = tuple(
            dict.fromkeys(fact_id for segment in segments for fact_id in segment.evidence_fact_ids)
        )
        if total_walking_distance > request.walking.max_total_distance_meters:
            issues.append(
                ItineraryEvaluationEngine._issue(
                    "walking",
                    "critical",
                    "TOTAL_WALKING_LIMIT_EXCEEDED",
                    "하루 누적 도보거리가 사용자 한도를 넘습니다.",
                    evidence_fact_ids=segment_fact_ids,
                )
            )
        if request.total_budget_krw is not None and total_cost_max > request.total_budget_krw:
            issues.append(
                ItineraryEvaluationEngine._issue(
                    "budget",
                    "critical",
                    "TOTAL_BUDGET_EXCEEDED",
                    "검증된 이동비용 합계가 총예산을 넘습니다.",
                    evidence_fact_ids=segment_fact_ids,
                )
            )
        continuous_minutes = 0
        maximum_continuous = 0
        for event in sorted(normalized, key=lambda item: item.start_at):
            duration = int((event.end_at - event.start_at).total_seconds() // 60)
            if event.type in {"rest", "meal"}:
                continuous_minutes = 0
            else:
                continuous_minutes += max(0, duration)
                maximum_continuous = max(maximum_continuous, continuous_minutes)
        if maximum_continuous > request.rest.max_continuous_activity_minutes:
            hard_rest = any(
                requirement == "required"
                for requirement in (
                    request.rest.seat_requirement,
                    request.rest.indoor_requirement,
                )
            )
            issues.append(
                ItineraryEvaluationEngine._issue(
                    "meal_rest",
                    "critical" if hard_rest else "low",
                    "MISSING_REQUIRED_REST" if hard_rest else "REST_RECOMMENDED",
                    "연속 활동 시간이 휴식 정책 한도를 넘습니다.",
                )
            )
        severities = {issue.severity for issue in issues}
        segment_risks = {segment.risk for segment in segments}
        unavailable_codes = {
            "ROUTE_EVIDENCE_MISSING",
            "PLACE_POSITION_UNRESOLVED",
            "USER_ROUTE_FACT_UNVERIFIABLE",
        }
        unknown_codes = {issue.reason_code for issue in issues if issue.severity == "unknown"}
        evidence_status: Literal["verified", "partial", "unavailable"] = (
            "unavailable"
            if unknown_codes & unavailable_codes
            else "partial"
            if unknown_codes
            else "verified"
        )
        if "critical" in severities or "critical" in segment_risks:
            status = "infeasible"
            overall_risk = "critical"
            timing_status: Literal["on_schedule", "at_risk", "disrupted", "unknown"] = "disrupted"
        elif evidence_status == "unavailable":
            status = "unverifiable"
            overall_risk = "unknown"
            timing_status = "unknown"
        elif "high" in segment_risks:
            status = "feasible_with_caution"
            overall_risk = "high"
            timing_status = "at_risk"
        elif "medium" in segment_risks:
            status = "feasible_with_caution"
            overall_risk = "medium"
            timing_status = "at_risk"
        elif evidence_status == "partial":
            status = "feasible_with_caution"
            overall_risk = "low"
            timing_status = "on_schedule"
        else:
            status = "feasible"
            overall_risk = "low"
            timing_status = "on_schedule"
        facts_reader = getattr(self._evidence, "evidence_facts", None)
        sources_reader = getattr(self._evidence, "data_sources", None)
        return EvaluationResponse(
            status=status,
            timing_status=timing_status,
            evidence_status=evidence_status,
            overall_risk=overall_risk,
            schedule_window_fit=(
                all(
                    request.activity_window.start_at <= event.start_at
                    and event.end_at <= request.activity_window.end_at
                    for event in normalized
                )
                and all(
                    previous.end_at <= current.start_at
                    for previous, current in zip(
                        sorted(normalized, key=lambda item: item.start_at),
                        sorted(normalized, key=lambda item: item.start_at)[1:],
                        strict=False,
                    )
                )
            ),
            normalized_schedule=NormalizedSchedule(events=tuple(normalized)),
            issues=tuple(issues),
            segment_evaluations=tuple(segments),
            activity_evaluations=self._activity_evaluations(normalized, issues, request),
            evidence_facts=facts_reader() if facts_reader is not None else (),
            data_sources=sources_reader() if sources_reader is not None else (),
            failure=(
                Failure(
                    code="DATA_NOT_READY",
                    message="필수 일정 사실을 검증할 수 없습니다.",
                    reason_codes=tuple(
                        dict.fromkeys(
                            issue.reason_code for issue in issues if issue.severity == "unknown"
                        )
                    ),
                    missing_capabilities=(),
                )
                if status == "unverifiable"
                else None
            ),
            total_distance_meters=sum(segment.distance_meters or 0 for segment in segments),
            total_cost=CostRange(
                min_krw=total_cost_min,
                max_krw=total_cost_max,
                is_estimated=any(segment.cost.is_estimated for segment in segments),
            ),
            totals=EvaluationTotals(
                walking_distance_meters=total_walking_distance,
                total_distance_meters=sum(segment.distance_meters or 0 for segment in segments),
                activity_minutes=sum(
                    max(0, int((event.end_at - event.start_at).total_seconds() // 60))
                    for event in normalized
                    if event.type in {"visit", "meal", "rest"}
                ),
                transfer_minutes=sum(
                    max(0, int((event.end_at - event.start_at).total_seconds() // 60))
                    for event in normalized
                    if event.type == "transfer"
                ),
                taxi_pickup_buffer_minutes=sum(
                    max(0, int((item.end_at - item.start_at).total_seconds() // 60))
                    for item in getattr(request, "timeline", ())
                    if item.type == "buffer"
                    and item.reason_code == "TAXI_PICKUP_PLANNING_BUFFER_APPLIED"
                ),
                bus_wait_minutes=sum(segment.bus_wait_minutes for segment in segments),
                transport_cost=CostRange(
                    min_krw=total_cost_min,
                    max_krw=total_cost_max,
                    is_estimated=any(segment.cost.is_estimated for segment in segments),
                ),
                return_slack_minutes=(
                    int(
                        (
                            request.activity_window.end_at
                            - max(event.end_at for event in normalized)
                        ).total_seconds()
                        // 60
                    )
                    if normalized
                    else None
                ),
            ),
            validation=ValidationSummary(
                schema_valid=True,
                timeline_valid=status != "infeasible",
                provenance_valid=status != "unverifiable",
                transit_connections_valid=all(segment.status == "feasible" for segment in segments),
                diversity_valid=True,
                checks=("HOTEL_ROUND_TRIP", "NO_SILENT_MODE_CHANGE"),
            ),
        )

    def _activity_evaluations(self, normalized, issues, request):
        results = []
        for event in normalized:
            if event.type not in {"visit", "meal", "rest"} or event.place_id is None:
                continue
            event_issues = [issue for issue in issues if event.event_id in issue.event_ids]
            opening_unknown = any(
                issue.reason_code == "OPENING_HOURS_UNKNOWN" for issue in event_issues
            )
            opening_conflict = any(
                issue.category == "opening_hours" and issue.reason_code != "OPENING_HOURS_UNKNOWN"
                for issue in event_issues
            )
            results.append(
                ActivityEvaluation(
                    event_id=event.event_id,
                    place_id=event.place_id,
                    operating_hours_status=(
                        "CONFLICT"
                        if opening_conflict
                        else "UNVERIFIABLE"
                        if opening_unknown
                        else "VERIFIED_OPEN"
                    ),
                    reason_codes=tuple(issue.reason_code for issue in event_issues),
                    evidence_fact_ids=tuple(
                        fact_id for issue in event_issues for fact_id in issue.evidence_fact_ids
                    ),
                )
            )
        return tuple(results)
