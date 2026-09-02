"""여섯 MCP 도구가 공유하는 application service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal, Protocol, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from jeju_trip.application.preparation import RequestPreparation, RequestPreparationFailure
from jeju_trip.application.readiness import ReadinessProvider
from jeju_trip.domain.models import (
    ActivitiesOnlyEvaluationInput,
    ActivityWindow,
    BusStopInspection,
    DayTripResponse,
    EvaluateJejuDayTripInput,
    EvaluationResponse,
    Failure,
    FullTimelineActivity,
    FullTimelineBuffer,
    FullTimelineEvaluationInput,
    FullTimelineTransfer,
    InspectBusStopInput,
    LegConstraint,
    PlaceDecision,
    PlaceReference,
    PlanningContext,
    PreviewTransferInput,
    PreviewTransferResponse,
    RecommendDayTripsInput,
    RecoveryOption,
    RevalidateJejuDayTripInput,
    RevalidationResponse,
    ScheduledActivity,
    SearchPlacesInput,
    SearchPlacesResponse,
    ValidationSummary,
)
from jeju_trip.planning.evaluation import EvaluationEvidence, ItineraryEvaluationEngine
from jeju_trip.planning.execution_budget import (
    ExecutionBudget,
    PlanningBudgetExceeded,
    PlanningTimeout,
)
from jeju_trip.planning.generation import DeterministicDayTripGenerator

KST = ZoneInfo("Asia/Seoul")


class PlanningGateway(Protocol):
    def recommend(self, request: RecommendDayTripsInput) -> DayTripResponse | None: ...

    def search_places(self, request: SearchPlacesInput) -> SearchPlacesResponse: ...

    def inspect_bus_stop(self, request: InspectBusStopInput) -> BusStopInspection: ...

    def preview_transfer(self, request: PreviewTransferInput) -> PreviewTransferResponse: ...


@dataclass(frozen=True)
class RealtimeEvidencePreparation:
    evidence: EvaluationEvidence | None
    warnings: tuple[str, ...] = ()


class RealtimeEvidenceProvider(Protocol):
    """다음 2개 버스 구간 또는 4시간 범위의 일시적 근거를 준비한다."""

    def prepare(
        self, request: RevalidateJejuDayTripInput, budget: ExecutionBudget
    ) -> RealtimeEvidencePreparation: ...


class EvaluationEvidenceFactory(Protocol):
    def __call__(self, request: EvaluateJejuDayTripInput) -> EvaluationEvidence: ...


class DisabledPlanningGateway:
    """필수 active publication 전에는 안전하게 capability를 닫는다."""

    def recommend(self, request: RecommendDayTripsInput) -> None:
        return None

    def search_places(self, request: SearchPlacesInput) -> SearchPlacesResponse:
        return SearchPlacesResponse(
            status="data_unavailable", reason_code="ACTIVE_PLACE_PUBLICATION_MISSING"
        )

    def inspect_bus_stop(self, request: InspectBusStopInput) -> BusStopInspection:
        return BusStopInspection(
            status="data_unavailable", reason_code="ACTIVE_BUS_STOP_PUBLICATION_MISSING"
        )

    def preview_transfer(self, request: PreviewTransferInput) -> PreviewTransferResponse:
        return PreviewTransferResponse(
            status="unavailable", reason_code="ROUTING_CAPABILITY_NOT_READY"
        )


class UnavailableEvaluationEvidence:
    """근거 publication이 없을 때 수치를 추정하지 않는 판정 경계."""

    def route(self, from_place_id: str, to_place_id: str, departure_at: datetime):
        return None

    def opening_window(self, place_id: str, on_date):
        return None


class TripPlannerService:
    def __init__(
        self,
        gateway: PlanningGateway | None = None,
        evaluation_evidence: EvaluationEvidence | None = None,
        generator: DeterministicDayTripGenerator | None = None,
        readiness: ReadinessProvider | None = None,
        realtime_evidence_provider: RealtimeEvidenceProvider | None = None,
        evaluation_evidence_factory: EvaluationEvidenceFactory | None = None,
        request_preparation: RequestPreparation | None = None,
    ) -> None:
        self._gateway = gateway or DisabledPlanningGateway()
        self._evaluation_evidence = evaluation_evidence or UnavailableEvaluationEvidence()
        self._evaluation_evidence_factory = evaluation_evidence_factory
        self._generator = generator
        self._readiness = readiness
        self._realtime_evidence_provider = realtime_evidence_provider
        self._request_preparation = request_preparation

    def recommend(self, request: RecommendDayTripsInput) -> DayTripResponse:
        if self._request_preparation is not None:
            try:
                request = self._request_preparation.prepare(request).request
            except RequestPreparationFailure as error:
                return self._generation_failure(
                    request,
                    code=error.code,
                    message="요청 장소를 제주 전역 active 장소에서 확정할 수 없습니다.",
                    reason_codes=error.reason_codes,
                    missing_capabilities=(),
                )
        if request.request_mode == "improve":
            return self._generation_failure(
                request,
                code="REQUEST_MODE_REMOVED",
                message=(
                    "request_mode=improve는 제거되었습니다. evaluate_jeju_day_trip을 사용하세요."
                ),
                reason_codes=("REQUEST_MODE_IMPROVE_REMOVED",),
                missing_capabilities=(),
            )
        if self._readiness is not None:
            gate = self._readiness.for_generation(request)
            if not gate.ready:
                return self._generation_failure(
                    request,
                    code="DATA_NOT_READY",
                    message="요청한 이동수단과 조건에 필요한 capability가 준비되지 않았습니다.",
                    reason_codes=("REQUIRED_CAPABILITY_UNAVAILABLE",),
                    missing_capabilities=gate.missing_capabilities,
                )
        if self._generator is not None:
            return self._generator.generate(request)
        response = self._gateway.recommend(request)
        if response is not None:
            return response
        return self._generation_failure(
            request,
            code="insufficient_feasible_routes",
            message="검증된 추천 세 개를 만들 데이터가 충분하지 않습니다.",
            reason_codes=(
                "OFFICIAL_TIMETABLE_PENDING",
                "ACTIVE_PLACE_PUBLICATION_MISSING",
                "CONFIRMED_STOP_MAPPING_MISSING",
            ),
            missing_capabilities=(
                "opening_hours",
                "future_bus_schedule",
                "confirmed_stop_identity",
            ),
        )

    def _generation_failure(
        self,
        request: RecommendDayTripsInput,
        *,
        code,
        message: str,
        reason_codes: tuple[str, ...],
        missing_capabilities: tuple[str, ...],
    ) -> DayTripResponse:
        now = datetime.now(UTC).astimezone(KST)
        trip_start = datetime.combine(request.trip_date, time.min, tzinfo=KST)
        days_before = max(0, (trip_start.date() - now.date()).days)
        expires_at = min(trip_start, now + timedelta(days=1))
        return DayTripResponse(
            request_id=f"req-{uuid4()}",
            generated_at=now,
            status="insufficient_feasible_routes",
            planning_context=PlanningContext(
                planned_at=now,
                trip_date=request.trip_date,
                days_before_trip=days_before,
                schedule_basis="service_calendar",
                plan_expires_at=expires_at,
                revalidate_at=(),
            ),
            request=request,
            place_decisions=tuple(
                PlaceDecision(
                    place_id=place.place_id,
                    requested_priority=cast(Literal["required", "preferred", "excluded"], priority),
                    decision="excluded" if priority == "excluded" else "unverifiable",
                    reason_codes=(
                        (
                            "USER_EXCLUDED"
                            if priority == "excluded"
                            else "REQUIRED_CAPABILITY_UNAVAILABLE"
                        ),
                    ),
                )
                for priority, places in (
                    ("required", request.required_places),
                    ("preferred", request.preferred_places),
                    ("excluded", request.excluded_places),
                )
                for place in places
            ),
            global_warnings=(
                "공식 운영시간·시간표·검증된 입구·정류장 mapping "
                "publication이 준비되지 않았습니다.",
            ),
            validation=ValidationSummary(
                schema_valid=True,
                timeline_valid=False,
                provenance_valid=False,
                transit_connections_valid=False,
                diversity_valid=False,
                checks=("NO_PARTIAL_SUCCESS", "NO_ESTIMATED_EXTERNAL_FACTS"),
            ),
            failure=Failure(
                code=code,
                message=message,
                reason_codes=reason_codes,
                missing_capabilities=missing_capabilities,
            ),
        )

    def evaluate(self, request: EvaluateJejuDayTripInput) -> EvaluationResponse:
        evidence = self._evidence_for(request)
        return ItineraryEvaluationEngine(evidence).evaluate(request, ExecutionBudget.evaluation())

    def revalidate(self, request: RevalidateJejuDayTripInput) -> RevalidationResponse:
        realtime_budget = ExecutionBudget.realtime()
        original_evaluation_budget = ExecutionBudget.evaluation()
        evaluation_evidence = self._evidence_for(request.itinerary)
        original = ItineraryEvaluationEngine(evaluation_evidence).evaluate(
            request.itinerary, original_evaluation_budget
        )
        uses_bus = any(segment.mode == "bus" for segment in original.segment_evaluations)
        waiting_for_bus = request.progress.state == "waiting_bus"
        moving = request.progress.state in {"walking", "waiting_bus", "on_bus", "in_taxi"}
        has_moving_context = bool(
            request.current_position
            or request.progress.current_stop_id
            or request.progress.current_route_id
        )
        if request.current_position is not None:
            location_basis = "gps"
        elif moving and not has_moving_context:
            location_basis = "unverifiable"
        else:
            location_basis = "event"
        planned_time = self._planned_progress_time(request)
        if request.progress.state == "at_place" and request.progress.current_event_started_at:
            delay = (
                max(
                    0,
                    int(
                        (request.progress.current_event_started_at - planned_time).total_seconds()
                        // 60
                    ),
                )
                if planned_time is not None
                else 0
            )
        else:
            delay = (
                max(
                    0,
                    int((request.progress.actual_time - planned_time).total_seconds() // 60),
                )
                if planned_time is not None
                else 0
            )
        warnings: tuple[str, ...] = ()
        if location_basis == "event":
            warnings = ("LOCATION_EVENT_BASED",)
        elif location_basis == "unverifiable":
            warnings = ("LOCATION_CONTEXT_INSUFFICIENT",)

        status: Literal["on_schedule", "at_risk", "disrupted", "data_unavailable"] = (
            "data_unavailable"
        )
        remaining: EvaluationResponse | None = None
        remaining_request: ActivitiesOnlyEvaluationInput | None = None
        if request.progress.state == "on_bus":
            status = "at_risk" if original.timing_status == "at_risk" else "on_schedule"
            remaining = None
            warnings = (*warnings, "ON_BUS_PROGRESS_UNSUPPORTED")
        else:
            remaining_request = self._remaining_request(request)
        if request.progress.state == "on_bus":
            pass
        elif location_basis == "unverifiable":
            status = "data_unavailable"
            remaining = None
        elif waiting_for_bus and (
            self._realtime_evidence_provider is None
            or (
                self._readiness is not None
                and not self._readiness.for_realtime(
                    uses_bus=True, trip_date=request.itinerary.trip_date
                ).ready
            )
        ):
            status = "data_unavailable"
            remaining = None
            warnings = (*warnings, "REALTIME_PROVIDER_UNAVAILABLE")
        elif remaining_request is None:
            status = "on_schedule" if original.status != "infeasible" else "disrupted"
            remaining = None
        else:
            remaining_evidence = self._evidence_for(remaining_request)
            preparation = (
                self._prepare_realtime_evidence(request, realtime_budget, remaining_evidence)
                if waiting_for_bus and self._realtime_evidence_provider is not None
                else RealtimeEvidencePreparation(remaining_evidence)
            )
            warnings = (*warnings, *preparation.warnings)
            if preparation.evidence is None:
                status = "data_unavailable"
                remaining = None
            else:
                try:
                    remaining = ItineraryEvaluationEngine(preparation.evidence).evaluate(
                        remaining_request, realtime_budget
                    )
                except (PlanningTimeout, PlanningBudgetExceeded) as error:
                    status = "data_unavailable"
                    remaining = None
                    warnings = (*warnings, str(error))
                else:
                    if remaining.timing_status == "disrupted":
                        status = "disrupted"
                    elif remaining.timing_status == "unknown":
                        status = "data_unavailable"
                    elif delay > 0 or remaining.timing_status == "at_risk":
                        status = "at_risk"
                    else:
                        status = "on_schedule"
        recovery_options = (
            tuple(
                RecoveryOption(
                    recovery_id=f"recovery-{repair.repair_id}",
                    action=cast(
                        Literal[
                            "WAIT_BUS",
                            "TAKE_TAXI",
                            "SHORTEN_STAY",
                            "SKIP_PREFERRED",
                            "TAXI_AFTER_BUS_TIMEOUT",
                        ],
                        {
                            "USE_FASTER_VERIFIED_ALTERNATIVE": "TAKE_TAXI",
                            "TAXI_AFTER_BUS_TIMEOUT": "TAXI_AFTER_BUS_TIMEOUT",
                            "SHORTEN_STAY": "SHORTEN_STAY",
                            "SKIP_OPTIONAL_ACTIVITY": "SKIP_PREFERRED",
                        }.get(repair.repair_type, "SHORTEN_STAY"),
                    ),
                    affected_event_ids=tuple(change.event_id for change in repair.changes),
                    result_status=(
                        "on_schedule" if repair.result_if_applied == "feasible" else "at_risk"
                    ),
                    evidence_fact_ids=repair.evidence_fact_ids,
                    expected_cost=repair.cost_increase,
                    reason_codes=(repair.repair_type,),
                    revalidated_evaluation=repair.revalidated_evaluation,
                )
                for repair in remaining.repair_options
            )
            if remaining is not None
            else ()
        )
        timeout_recovery = self._taxi_after_bus_timeout(
            request, evaluation_evidence, realtime_budget
        )
        if timeout_recovery is not None:
            recovery_options = (timeout_recovery, *recovery_options)
        return RevalidationResponse(
            status=status,
            checked_at=request.checked_at,
            delay_minutes=delay,
            location_basis=location_basis,
            original_evaluation=original,
            remaining_evaluation=remaining,
            recovery_options=recovery_options[:5],
            timing_status=(
                "unknown"
                if status == "data_unavailable"
                else cast(Literal["on_schedule", "at_risk", "disrupted"], status)
            ),
            evidence_status=(
                "unavailable"
                if status == "data_unavailable"
                else "partial"
                if request.progress.state == "on_bus"
                or original.evidence_status == "partial"
                or (remaining is not None and remaining.evidence_status == "partial")
                else "verified"
            ),
            warnings=warnings,
            failure=(
                Failure(
                    code="DATA_NOT_READY",
                    message="실시간 남은 일정을 검증할 근거가 준비되지 않았습니다.",
                    reason_codes=warnings or ("REALTIME_DATA_UNAVAILABLE",),
                    missing_capabilities=("realtime_bus_ready",) if uses_bus else (),
                )
                if status == "data_unavailable"
                else None
            ),
        )

    @staticmethod
    def _taxi_after_bus_timeout(
        request: RevalidateJejuDayTripInput,
        evidence: EvaluationEvidence,
        budget: ExecutionBudget,
    ) -> RecoveryOption | None:
        """현재 버스 대기만 검증된 택시 대안으로 교체하고 전체 타임라인을 재평가한다."""

        itinerary = request.itinerary
        if request.progress.state != "waiting_bus" or not isinstance(
            itinerary, FullTimelineEvaluationInput
        ):
            return None
        index = next(
            (
                position
                for position, item in enumerate(itinerary.timeline)
                if isinstance(item, FullTimelineTransfer)
                and item.event_id == request.progress.current_event_id
                and item.planned_mode == "bus"
            ),
            None,
        )
        if index is None:
            return None
        original = itinerary.timeline[index]
        assert isinstance(original, FullTimelineTransfer)
        waited = max(0, int((request.checked_at - original.start_at).total_seconds() // 60))
        if waited <= itinerary.transport.bus_wait_limit_minutes:
            return None
        taxi = next(
            (
                alternative
                for alternative in original.route_alternatives
                if alternative.mode == "taxi"
                and alternative.status == "feasible"
                and alternative.duration_minutes is not None
                and alternative.cost is not None
                and alternative.evidence_fact_ids
            ),
            None,
        )
        if taxi is None or taxi.duration_minutes is None or taxi.cost is None:
            return None
        pickup = taxi.pickup_buffer_minutes or itinerary.transport.taxi_pickup_buffer_minutes
        driving = taxi.driving_minutes or taxi.duration_minutes - pickup
        if driving <= 0:
            return None
        replacement_items = list(itinerary.timeline[:index])
        if request.checked_at > original.start_at:
            replacement_items.append(
                FullTimelineBuffer(
                    event_id=f"{original.event_id}-elapsed-wait",
                    type="buffer",
                    start_at=original.start_at,
                    end_at=request.checked_at,
                    place=original.from_place,
                    reason_code="BUS_WAIT_ELAPSED",
                    evidence_fact_ids=original.evidence_fact_ids,
                )
            )
        pickup_end = request.checked_at + timedelta(minutes=pickup)
        replacement_items.append(
            FullTimelineBuffer(
                event_id=f"{original.event_id}-taxi-pickup",
                type="buffer",
                start_at=request.checked_at,
                end_at=pickup_end,
                place=original.from_place,
                reason_code="TAXI_PICKUP_PLANNING_BUFFER_APPLIED",
                evidence_fact_ids=taxi.evidence_fact_ids,
            )
        )
        replacement = FullTimelineTransfer(
            event_id=f"{original.event_id}-taxi",
            type="transfer",
            start_at=pickup_end,
            end_at=pickup_end + timedelta(minutes=driving),
            from_place=original.from_place,
            to_place=original.to_place,
            planned_mode="taxi",
            planned_distance_meters=taxi.distance_meters,
            route_alternatives=original.route_alternatives,
            evidence_fact_ids=taxi.evidence_fact_ids,
        )
        replacement_items.append(replacement)
        shift = replacement.end_at - original.end_at
        replacement_items.extend(
            item.model_copy(
                update={"start_at": item.start_at + shift, "end_at": item.end_at + shift}
            )
            for item in itinerary.timeline[index + 1 :]
        )
        repaired_request = itinerary.model_copy(update={"timeline": tuple(replacement_items)})
        try:
            repaired = ItineraryEvaluationEngine(evidence).evaluate(repaired_request, budget)
        except (PlanningTimeout, PlanningBudgetExceeded):
            return None
        if repaired.status not in {"feasible", "feasible_with_caution"}:
            return None
        return RecoveryOption(
            recovery_id=f"recovery-taxi-after-{original.event_id}",
            action="TAXI_AFTER_BUS_TIMEOUT",
            affected_event_ids=(original.event_id,),
            result_status=("on_schedule" if repaired.timing_status == "on_schedule" else "at_risk"),
            evidence_fact_ids=taxi.evidence_fact_ids,
            replacement_transfer=replacement,
            expected_duration_minutes=taxi.duration_minutes,
            expected_distance_meters=taxi.distance_meters,
            expected_cost=taxi.cost,
            reason_codes=("BUS_WAIT_LIMIT_EXCEEDED", "TAXI_AFTER_BUS_TIMEOUT"),
            revalidated_evaluation=repaired,
        )

    def _evidence_for(self, request: EvaluateJejuDayTripInput) -> EvaluationEvidence:
        if self._readiness is not None and not self._readiness.for_evaluation(request).ready:
            return UnavailableEvaluationEvidence()
        if self._evaluation_evidence_factory is not None:
            return self._evaluation_evidence_factory(request)
        return self._evaluation_evidence

    def _prepare_realtime_evidence(
        self,
        request: RevalidateJejuDayTripInput,
        budget: ExecutionBudget,
        base: EvaluationEvidence,
    ) -> RealtimeEvidencePreparation:
        if self._realtime_evidence_provider is None:
            return RealtimeEvidencePreparation(None)
        prepare_with_base = getattr(self._realtime_evidence_provider, "prepare_with_base", None)
        if prepare_with_base is not None:
            return prepare_with_base(request, budget, base)
        return self._realtime_evidence_provider.prepare(request, budget)

    @staticmethod
    def _planned_progress_time(request: RevalidateJejuDayTripInput) -> datetime | None:
        itinerary = request.itinerary
        if isinstance(itinerary, ActivitiesOnlyEvaluationInput):
            event = next(
                (
                    item
                    for item in itinerary.scheduled_activities
                    if item.event_id == request.progress.current_event_id
                ),
                None,
            )
        else:
            event = next(
                (
                    item
                    for item in itinerary.timeline
                    if item.event_id == request.progress.current_event_id
                ),
                None,
            )
        if event is None:
            return None
        if request.progress.state == "ready_to_depart":
            return event.end_at
        return event.start_at

    @staticmethod
    def _remaining_request(
        request: RevalidateJejuDayTripInput,
    ) -> ActivitiesOnlyEvaluationInput | None:
        itinerary = request.itinerary
        excluded = set(request.progress.completed_event_ids)
        if request.progress.state == "ready_to_depart":
            excluded.add(request.progress.current_event_id)
        if isinstance(itinerary, ActivitiesOnlyEvaluationInput):
            activities = tuple(
                item
                for item in itinerary.scheduled_activities
                if item.event_id not in excluded and item.end_at > request.checked_at
            )
        else:
            activities = tuple(
                ScheduledActivity(
                    event_id=item.event_id,
                    type=item.type,
                    place=item.place,
                    start_at=item.start_at,
                    end_at=item.end_at,
                    required=item.required,
                )
                for item in itinerary.timeline
                if isinstance(item, FullTimelineActivity)
                and item.event_id not in excluded
                and item.end_at > request.checked_at
            )
        if (
            request.progress.state == "at_place"
            and request.progress.current_event_started_at is not None
        ):
            adjusted: list[ScheduledActivity] = []
            for item in activities:
                if item.event_id != request.progress.current_event_id:
                    adjusted.append(item)
                    continue
                planned_stay = int((item.end_at - item.start_at).total_seconds() // 60)
                elapsed = max(
                    0,
                    int(
                        (
                            request.checked_at - request.progress.current_event_started_at
                        ).total_seconds()
                        // 60
                    ),
                )
                remaining_stay = max(0, planned_stay - elapsed)
                if remaining_stay:
                    adjusted.append(
                        item.model_copy(
                            update={
                                "start_at": request.checked_at,
                                "end_at": request.checked_at + timedelta(minutes=remaining_stay),
                            }
                        )
                    )
            activities = tuple(adjusted)
        leg_constraints: tuple[LegConstraint, ...] = ()
        if not isinstance(itinerary, ActivitiesOnlyEvaluationInput):
            remaining_ids = {item.event_id for item in activities}
            constraints = []
            for index, item in enumerate(itinerary.timeline):
                if not isinstance(item, FullTimelineTransfer):
                    continue
                previous = next(
                    (
                        candidate
                        for candidate in reversed(itinerary.timeline[:index])
                        if isinstance(candidate, FullTimelineActivity)
                    ),
                    None,
                )
                following = next(
                    (
                        candidate
                        for candidate in itinerary.timeline[index + 1 :]
                        if isinstance(candidate, FullTimelineActivity)
                    ),
                    None,
                )
                if (
                    item.event_id == request.progress.current_event_id
                    and following is not None
                    and following.event_id in remaining_ids
                ):
                    constraints.append(
                        LegConstraint(
                            from_event_id=None,
                            to_event_id=following.event_id,
                            locked_mode=item.planned_mode,
                        )
                    )
                elif (
                    previous is not None
                    and following is not None
                    and previous.event_id in remaining_ids
                    and following.event_id in remaining_ids
                ):
                    constraints.append(
                        LegConstraint(
                            from_event_id=previous.event_id,
                            to_event_id=following.event_id,
                            locked_mode=item.planned_mode,
                        )
                    )
                elif (
                    previous is not None
                    and previous.event_id == request.progress.current_event_id
                    and previous.event_id not in remaining_ids
                    and following is not None
                    and following.event_id in remaining_ids
                ):
                    constraints.append(
                        LegConstraint(
                            from_event_id=None,
                            to_event_id=following.event_id,
                            locked_mode=item.planned_mode,
                        )
                    )
                elif (
                    previous is not None
                    and previous.event_id in remaining_ids
                    and following is None
                ):
                    constraints.append(
                        LegConstraint(
                            from_event_id=previous.event_id,
                            to_event_id=None,
                            locked_mode=item.planned_mode,
                        )
                    )
            leg_constraints = tuple(constraints)
        if not activities or request.checked_at >= itinerary.activity_window.end_at:
            return None
        current_place_id = request.progress.current_place_id
        if current_place_id is None and request.current_position is None:
            return None
        start_location = PlaceReference(
            place_id=current_place_id or "current:gps",
            name="현재 진행 위치",
            coordinates=request.current_position,
        )
        return ActivitiesOnlyEvaluationInput(
            trip_date=itinerary.trip_date,
            timezone=itinerary.timezone,
            accommodation=itinerary.accommodation,
            activity_window=ActivityWindow(
                start_at=request.checked_at,
                end_at=itinerary.activity_window.end_at,
            ),
            day_boundary=itinerary.day_boundary,
            place_duration_preferences=itinerary.place_duration_preferences,
            party=itinerary.party,
            transport=itinerary.transport,
            walking=itinerary.walking,
            rest=itinerary.rest,
            food=itinerary.food,
            total_budget_krw=itinerary.total_budget_krw,
            schedule_format="activities_only",
            start_location=start_location,
            scheduled_activities=activities,
            leg_constraints=leg_constraints,
        )

    def search_places(self, request: SearchPlacesInput) -> SearchPlacesResponse:
        return self._gateway.search_places(request)

    def inspect_bus_stop(self, request: InspectBusStopInput) -> BusStopInspection:
        return self._gateway.inspect_bus_stop(request)

    def preview_transfer(self, request: PreviewTransferInput) -> PreviewTransferResponse:
        if self._readiness is not None and not self._readiness.for_transfer(request).ready:
            return PreviewTransferResponse(
                status="unavailable",
                reason_code="REQUIRED_CAPABILITY_UNAVAILABLE",
            )
        return self._gateway.preview_transfer(request)
