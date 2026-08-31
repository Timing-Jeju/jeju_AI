"""MCP 입력과 출력의 유일한 Pydantic 계약 원본."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.7.0"
KST_NAME = "Asia/Seoul"
KST_OFFSET = timedelta(hours=9)


class ContractModel(BaseModel):
    """알 수 없는 필드를 거부하는 공개 계약 기본형."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Coordinates(ContractModel):
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]


class PlaceReference(ContractModel):
    place_id: str | None = None
    name: Annotated[str | None, Field(min_length=1)] = None
    address: str | None = None
    coordinates: Coordinates | None = None

    @model_validator(mode="after")
    def require_identity(self) -> PlaceReference:
        if self.place_id is None and self.name is None:
            raise ValueError("place_id or name is required")
        return self


class AccommodationInput(ContractModel):
    place_id: str | None = None
    name: Annotated[str, Field(min_length=1)]
    address: str | None = None
    coordinates: Coordinates | None = None


# v0.3 내부 호출자의 점진적 전환을 위한 이름 호환성이다.
PlaceInput = AccommodationInput


def _require_kst(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != KST_OFFSET:
        raise ValueError(f"{field_name} must include +09:00 offset")


class ActivityWindow(ContractModel):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def validate_window(self) -> ActivityWindow:
        _require_kst(self.start_at, "activity_window.start_at")
        _require_kst(self.end_at, "activity_window.end_at")
        if self.end_at <= self.start_at:
            raise ValueError("activity window end must follow start")
        if self.end_at - self.start_at > timedelta(hours=24):
            raise ValueError("activity window cannot exceed 24 hours")
        return self


class DayBoundary(ContractModel):
    """숙소 왕복으로 제한하지 않는 하루의 canonical 시작·종료 장소."""

    start_place: PlaceReference
    end_place: PlaceReference


class PlaceDurationPreference(ContractModel):
    """사용자가 장소별로 고정한 체류시간 제약."""

    place_id: Annotated[str, Field(min_length=1)]
    requested_stay_minutes: Annotated[int, Field(gt=0, le=24 * 60)]


class BoundaryTripInput(ContractModel):
    """생성·판정이 공유하는 활동창, 숙소, 하루 경계와 체류시간 입력."""

    accommodation: AccommodationInput
    activity_window: ActivityWindow
    day_boundary: DayBoundary | None = None
    place_duration_preferences: tuple[PlaceDurationPreference, ...] = Field(
        default=(), max_length=40
    )

    @model_validator(mode="after")
    def validate_duration_preferences(self) -> BoundaryTripInput:
        place_ids = tuple(item.place_id for item in self.place_duration_preferences)
        if len(place_ids) != len(set(place_ids)):
            raise ValueError("place duration preference IDs must be unique")
        return self

    @property
    def start_boundary(self) -> PlaceReference:
        if self.day_boundary is not None:
            return self.day_boundary.start_place
        return PlaceReference(
            place_id=self.accommodation.place_id,
            name=self.accommodation.name,
            address=self.accommodation.address,
            coordinates=self.accommodation.coordinates,
        )

    @property
    def end_boundary(self) -> PlaceReference:
        if self.day_boundary is not None:
            return self.day_boundary.end_place
        return PlaceReference(
            place_id=self.accommodation.place_id,
            name=self.accommodation.name,
            address=self.accommodation.address,
            coordinates=self.accommodation.coordinates,
        )

    def requested_stay_minutes(self, place_id: str) -> int | None:
        return next(
            (
                item.requested_stay_minutes
                for item in self.place_duration_preferences
                if item.place_id == place_id
            ),
            None,
        )


class Party(ContractModel):
    adults: Annotated[int, Field(ge=0)] = 1
    children: Annotated[int, Field(ge=0)] = 0
    seniors: Annotated[int, Field(ge=0)] = 0
    mobility_support_required: bool = False

    @model_validator(mode="after")
    def require_person(self) -> Party:
        if self.adults + self.children + self.seniors < 1:
            raise ValueError("party must contain at least one person")
        return self


class DiscoveryPreferences(ContractModel):
    allow_additional_attractions: bool = True
    preferred_categories: tuple[str, ...] = ()
    maximum_additional_places: Annotated[int, Field(ge=0, le=10)] = 3


class TransportPreferences(ContractModel):
    allowed_modes: set[Literal["walk", "bus", "taxi"]] = Field(
        default_factory=lambda: {"walk", "bus", "taxi"}
    )
    preferred_mode: Literal["walk", "bus", "taxi"] = "bus"
    selection_policy: Literal[
        "prefer_selected",
        "fastest",
        "lowest_cost",
        "least_walking",
        "fewest_transfers",
        "balanced",
        "service_priority",
        "cost_time_balance",
    ] = "cost_time_balance"
    fallback_order: tuple[Literal["walk", "bus", "taxi"], ...] = ("taxi", "walk")
    max_transfers_per_leg: Annotated[int, Field(ge=0, le=4)] = 1
    direct_walk_limit_minutes: Annotated[int, Field(ge=0)] = 15
    bus_wait_limit_minutes: Annotated[int, Field(ge=0)] = 30
    max_bus_extra_minutes: Annotated[int, Field(ge=0)] = 30
    min_bus_savings_krw: Annotated[int, Field(ge=0)] = 5_000
    taxi_pickup_buffer_minutes: Annotated[int, Field(ge=0)] = 10

    @model_validator(mode="before")
    @classmethod
    def choose_defaults_within_allowed_modes(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "allowed_modes" not in value:
            return value
        normalized = dict(value)
        allowed = set(normalized["allowed_modes"])
        if "preferred_mode" not in normalized:
            normalized["preferred_mode"] = next(
                (mode for mode in ("bus", "taxi", "walk") if mode in allowed),
                "bus",
            )
        if "fallback_order" not in normalized:
            preferred = normalized["preferred_mode"]
            normalized["fallback_order"] = [
                mode for mode in ("taxi", "walk", "bus") if mode in allowed and mode != preferred
            ]
        return normalized

    @model_validator(mode="after")
    def validate_modes(self) -> TransportPreferences:
        if not self.allowed_modes:
            raise ValueError("at least one allowed mode is required")
        if self.preferred_mode not in self.allowed_modes:
            raise ValueError("preferred_mode must be allowed")
        if any(mode not in self.allowed_modes for mode in self.fallback_order):
            raise ValueError("fallback modes must be allowed")
        return self


class WalkingPreferences(ContractModel):
    allow_direct_walk: bool = True
    max_access_walk_minutes: Annotated[int, Field(ge=0)] = 20
    max_total_distance_meters: Annotated[int, Field(ge=0)] = 6000
    max_single_leg_minutes: Annotated[int, Field(ge=0)] = 25
    avoid_stairs_required: bool = False


class RestPreferences(ContractModel):
    pace: Literal["relaxed", "normal", "packed"] = "normal"
    minimum_break_minutes: Annotated[int, Field(ge=0)] = 20
    max_continuous_activity_minutes: Annotated[int, Field(gt=0)] = 120
    seat_requirement: Literal["required", "preferred", "not_needed"] = "not_needed"
    restroom_requirement: Literal["required", "preferred", "not_needed"] = "not_needed"
    indoor_requirement: Literal["required", "preferred", "not_needed"] = "not_needed"
    unreserved_bus_seat_counts_as_rest: Literal[False] = False


class FoodPreferences(ContractModel):
    auto_schedule_meals: bool = True
    auto_schedule_cafe: bool = True
    excluded_foods: tuple[str, ...] = ()
    allergens: tuple[str, ...] = ()
    preferred_cuisines: tuple[str, ...] = ()


class MultiDayPreferences(ContractModel):
    """최대 5일 여행에서 날짜 사이에 적용할 결정론적 정책."""

    avoid_repeated_visits: Literal[True] = True
    soft_avoid_repeated_meals_and_rests: bool = True
    minimum_overnight_rest_minutes: Annotated[int, Field(ge=0)] = 600
    trip_transport_budget_krw: Annotated[int | None, Field(ge=0)] = None


class SelectedDayHistory(ContractModel):
    """사용자가 실제로 선택한 이전 날짜의 완전한 계획."""

    day_conditions: CommonTripInput
    selected_recommendation: Recommendation

    @model_validator(mode="after")
    def validate_history_day(self) -> SelectedDayHistory:
        timeline = self.selected_recommendation.timeline
        if len(timeline) > 40:
            raise ValueError("previous day timeline cannot exceed 40 events")
        if not timeline or any(
            event.start_at.date() != self.day_conditions.trip_date for event in timeline
        ):
            raise ValueError("previous day timeline date must match day_conditions.trip_date")
        return self


class RecommendDayTripsInput(BoundaryTripInput):
    schema_version: Literal["0.7.0"] = SCHEMA_VERSION
    request_mode: Literal["generate"] = "generate"
    trip_date: date
    timezone: Literal["Asia/Seoul"] = KST_NAME
    party: Party = Field(default_factory=Party)
    transport: TransportPreferences = Field(default_factory=TransportPreferences)
    walking: WalkingPreferences = Field(default_factory=WalkingPreferences)
    rest: RestPreferences = Field(default_factory=RestPreferences)
    required_places: tuple[PlaceReference, ...] = Field(default=(), max_length=10)
    preferred_places: tuple[PlaceReference, ...] = Field(default=(), max_length=30)
    excluded_places: tuple[PlaceReference, ...] = ()
    discovery: DiscoveryPreferences = Field(default_factory=DiscoveryPreferences)
    food: FoodPreferences = Field(default_factory=FoodPreferences)
    total_budget_krw: Annotated[int | None, Field(ge=0)] = None
    original_text: str | None = None
    previous_days: tuple[SelectedDayHistory, ...] = Field(default=(), max_length=4)
    multi_day: MultiDayPreferences = Field(default_factory=MultiDayPreferences)

    @model_validator(mode="after")
    def validate_request_time(self) -> RecommendDayTripsInput:
        if self.activity_window.start_at.date() != self.trip_date:
            raise ValueError("activity_window.start_at must use trip_date")
        dates = tuple(day.day_conditions.trip_date for day in self.previous_days)
        if len(dates) != len(set(dates)):
            raise ValueError("previous day dates must be unique")
        if dates != tuple(sorted(dates)):
            raise ValueError("previous day dates must be strictly ascending")
        if any(day >= self.trip_date for day in dates):
            raise ValueError("previous days must be earlier than trip_date")
        visited = {
            event.place_id or (event.visit.place_id if event.visit is not None else None)
            for day in self.previous_days
            for event in day.selected_recommendation.timeline
            if event.type == "visit"
        }
        visited.discard(None)
        required = {place.place_id for place in self.required_places if place.place_id is not None}
        if visited & required:
            raise ValueError("PLACE_ALREADY_VISITED_CONFLICT")
        return self


class SourceRef(ContractModel):
    source_id: str
    publication_id: str | None = None
    source_fact_id: str | None = None


class Derivation(ContractModel):
    kind: Literal["source", "policy", "computed"]
    formula: str | None = None
    input_fact_ids: tuple[str, ...] = ()


class EvidenceFact(ContractModel):
    fact_id: str
    category: str
    value: Any
    unit: str | None = None
    source_refs: tuple[SourceRef, ...] = ()
    data_as_of: date | datetime
    retrieved_at: datetime
    confidence: Annotated[float, Field(ge=0, le=1)]
    is_estimated: bool = False
    derivation: Derivation

    @model_validator(mode="after")
    def source_fact_needs_source(self) -> EvidenceFact:
        if self.derivation.kind == "source" and not self.source_refs:
            raise ValueError("source evidence must include source_refs")
        return self


class Assumption(ContractModel):
    field: str
    value: Any
    reason: str


class DataSourceMetadata(ContractModel):
    source_id: str
    provider: str
    dataset_version: str | None
    data_as_of: date | datetime | None
    retrieved_at: datetime
    status: Literal["ACTIVE", "PENDING", "STALE", "UNAVAILABLE"]
    attribution_text: str


_EVIDENCE_REFERENCE_FIELDS = frozenset(
    {
        "evidence_fact_ids",
        "derivation_evidence_fact_ids",
        "distance_derivation_fact_ids",
        "new_evidence_fact_ids",
    }
)
_NESTED_EVIDENCE_LEDGER_FIELDS = frozenset(
    {"original_evaluation", "remaining_evaluation", "revalidated_evaluation"}
)


def _collect_evidence_references(value: Any) -> set[str]:
    """공개 모델 subtree의 모든 evidence ID 필드를 이름 기반으로 빠짐없이 수집한다."""

    references: set[str] = set()
    if isinstance(value, ContractModel):
        for field_name in type(value).model_fields:
            field_value = getattr(value, field_name)
            if field_name in _EVIDENCE_REFERENCE_FIELDS:
                references.update(str(item) for item in field_value)
            elif field_name not in _NESTED_EVIDENCE_LEDGER_FIELDS:
                references.update(_collect_evidence_references(field_value))
    elif isinstance(value, dict):
        for item in value.values():
            references.update(_collect_evidence_references(item))
    elif isinstance(value, (tuple, list, set, frozenset)):
        for item in value:
            references.update(_collect_evidence_references(item))
    return references


def _validate_evidence_closure(
    evidence_facts: tuple[EvidenceFact, ...],
    referenced_values: tuple[Any, ...],
    *,
    allow_duplicate_fact_ids: bool = False,
) -> None:
    """ledger derivation과 공개 필드 참조가 모두 응답 내부 fact로 닫히는지 검증한다."""

    fact_ids = [fact.fact_id for fact in evidence_facts]
    facts = set(fact_ids)
    if not allow_duplicate_fact_ids and len(facts) != len(fact_ids):
        raise ValueError("evidence ledger contains duplicate fact IDs")
    for fact in evidence_facts:
        unknown_inputs = set(fact.derivation.input_fact_ids) - facts
        if unknown_inputs:
            raise ValueError(
                f"evidence derivation references unknown evidence facts: {sorted(unknown_inputs)}"
            )
    references: set[str] = set()
    for value in referenced_values:
        references.update(_collect_evidence_references(value))
    unknown_references = references - facts
    if unknown_references:
        raise ValueError(
            f"response references unknown evidence facts: {sorted(unknown_references)}"
        )


def _validate_source_lineage(
    evidence_facts: tuple[EvidenceFact, ...],
    data_sources: tuple[DataSourceMetadata, ...],
) -> None:
    """모든 evidence SourceRef를 같은 응답의 source metadata ledger에 닫는다."""

    source_ids = [source.source_id for source in data_sources]
    known_sources = set(source_ids)
    if len(source_ids) != len(known_sources):
        raise ValueError("data source ledger contains duplicate source IDs")
    referenced_sources = {
        source_ref.source_id
        for fact in evidence_facts
        for source_ref in fact.source_refs
    }
    unknown_sources = referenced_sources - known_sources
    if unknown_sources:
        raise ValueError(
            f"evidence references unknown data source: {sorted(unknown_sources)}"
        )


class EndpointBasis(StrEnum):
    VERIFIED_ENTRANCE = "VERIFIED_ENTRANCE"
    REPRESENTATIVE_PLACE_POINT = "REPRESENTATIVE_PLACE_POINT"
    CONFIRMED_STOP = "CONFIRMED_STOP"
    CURRENT_GPS = "CURRENT_GPS"


class ModeReasonCode(StrEnum):
    """구간별 수단 선택·탈락을 집계하는 안정적인 공개 코드."""

    BUS_SELECTED_FASTER_OR_EQUAL = "BUS_SELECTED_FASTER_OR_EQUAL"
    BUS_SELECTED_FOR_SAVINGS = "BUS_SELECTED_FOR_SAVINGS"
    BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED = "BUS_ROUTE_EXISTS_STOP_TIME_UNVERIFIED"
    BUS_ACCESS_WALK_EXCEEDED = "BUS_ACCESS_WALK_EXCEEDED"
    BUS_WAIT_LIMIT_EXCEEDED = "BUS_WAIT_LIMIT_EXCEEDED"
    BUS_SAVINGS_BELOW_THRESHOLD = "BUS_SAVINGS_BELOW_THRESHOLD"
    BUS_EXTRA_TIME_EXCEEDED = "BUS_EXTRA_TIME_EXCEEDED"
    BUS_SERVICE_DAY_UNVERIFIED = "BUS_SERVICE_DAY_UNVERIFIED"
    BUS_ROUTE_UNAVAILABLE = "BUS_ROUTE_UNAVAILABLE"
    WALK_SELECTED_WITHIN_THRESHOLD = "WALK_SELECTED_WITHIN_THRESHOLD"
    WALK_LIMIT_EXCEEDED = "WALK_LIMIT_EXCEEDED"
    TAXI_SELECTED_FASTER = "TAXI_SELECTED_FASTER"
    MODE_NOT_ALLOWED = "MODE_NOT_ALLOWED"


class ModeDecision(ContractModel):
    """수단 선택의 정책 입력과 검증 결과를 보존한다."""

    policy_id: Literal["service-priority-v1", "cost-time-balance-v1"] = "service-priority-v1"
    selected_mode: Literal["walk", "bus", "taxi"]
    origin_basis: EndpointBasis
    destination_basis: EndpointBasis
    context: Literal["generation", "revalidation"] = "generation"
    walk_threshold_minutes: Annotated[int, Field(ge=0)] = 15
    bus_wait_threshold_minutes: Annotated[int, Field(ge=0)] = 30
    max_bus_extra_minutes: Annotated[int, Field(ge=0)] = 30
    min_bus_savings_krw: Annotated[int, Field(ge=0)] = 5_000
    planned_walk_minutes: Annotated[int | None, Field(ge=0)] = None
    bus_wait_minutes: Annotated[int | None, Field(ge=0)] = None
    bus_door_to_door_minutes: Annotated[int | None, Field(ge=0)] = None
    taxi_pickup_buffer_minutes: Annotated[int | None, Field(ge=0)] = None
    taxi_driving_minutes: Annotated[int | None, Field(ge=0)] = None
    taxi_door_to_door_minutes: Annotated[int | None, Field(ge=0)] = None
    bus_cost: CostRange | None = None
    taxi_cost: CostRange | None = None
    cost_midpoint_savings_krw: int | None = None
    cost_savings_min_krw: int | None = None
    cost_savings_max_krw: int | None = None
    bus_extra_minutes: int | None = None
    bus_time_threshold_margin_minutes: int | None = None
    bus_savings_threshold_margin_krw: int | None = None
    selection_near_threshold: bool = False
    deadline_slack_minutes: int | None = None
    reason_codes: tuple[str, ...] = Field(min_length=1)
    unselected_reason_codes: dict[Literal["walk", "bus", "taxi"], tuple[str, ...]] = Field(
        default_factory=dict
    )
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def restrict_current_gps(self) -> ModeDecision:
        if self.context == "generation" and EndpointBasis.CURRENT_GPS in {
            self.origin_basis,
            self.destination_basis,
        }:
            raise ValueError("CURRENT_GPS is only allowed for revalidation origin")
        if self.destination_basis == EndpointBasis.CURRENT_GPS:
            raise ValueError("CURRENT_GPS cannot be a destination")
        return self


class WalkConnection(ContractModel):
    kind: Literal["access_walk", "egress_walk", "transfer_walk", "direct_walk"]
    from_id: str
    to_id: str
    distance_meters: Annotated[int, Field(ge=0)]
    expected_minutes: Annotated[int, Field(ge=0)]
    speed_multiplier: Annotated[float, Field(ge=1)]
    route_uncertainty_minutes: Annotated[int, Field(ge=0)]
    planned_minutes: Annotated[int, Field(ge=0)]
    entrance_verification: Literal["VERIFIED", "PROVISIONAL_PLACE_POINT"]
    stairs_status: Literal["CLEAR", "PRESENT", "UNKNOWN"] = "UNKNOWN"
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)


class BusRide(ContractModel):
    canonical_boarding_stop_id: str
    provider_boarding_stop_id: str
    boarding_stop_name: str
    boarding_stop_position: Coordinates
    boarding_direction: str
    canonical_alighting_stop_id: str
    provider_alighting_stop_id: str
    alighting_stop_name: str
    alighting_stop_position: Coordinates
    route_id: str
    route_number: str
    scheduled_departure_at: datetime
    scheduled_arrival_at: datetime
    recommended_stop_arrival_at: datetime
    boarding_buffer_minutes: Annotated[int, Field(gt=0)]
    mapping_status: Literal["CONFIRMED"]
    distance_meters: Annotated[int, Field(ge=0)] = 0
    route_stop_polyline_estimate: bool = True
    distance_derivation_fact_ids: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_boarding_buffer(self) -> BusRide:
        _require_kst(self.scheduled_departure_at, "scheduled bus departure")
        _require_kst(self.scheduled_arrival_at, "scheduled bus arrival")
        _require_kst(self.recommended_stop_arrival_at, "recommended stop arrival")
        delta = self.scheduled_departure_at - self.recommended_stop_arrival_at
        if int(delta.total_seconds() // 60) < self.boarding_buffer_minutes:
            raise ValueError("recommended stop arrival does not satisfy boarding buffer")
        if self.scheduled_arrival_at <= self.scheduled_departure_at:
            raise ValueError("scheduled arrival must follow departure")
        return self


class TaxiAlternative(ContractModel):
    duration_minutes: Annotated[int, Field(gt=0)]
    distance_meters: Annotated[int, Field(gt=0)]
    fare_min_krw: Annotated[int, Field(ge=0)]
    fare_max_krw: Annotated[int, Field(ge=0)]
    is_estimated: Literal[True] = True
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)


class RouteAlternativeSummary(ContractModel):
    mode: Literal["walk", "bus", "taxi"]
    duration_minutes: Annotated[int | None, Field(gt=0)] = None
    walking_minutes: Annotated[int, Field(ge=0)]
    cost: CostRange | None = None
    transfers: Annotated[int, Field(ge=0)] = 0
    status: Literal["feasible", "unverifiable", "unavailable"]
    distance_meters: Annotated[int | None, Field(ge=0)] = None
    wait_minutes: Annotated[int | None, Field(ge=0)] = None
    pickup_buffer_minutes: Annotated[int | None, Field(ge=0)] = None
    driving_minutes: Annotated[int | None, Field(ge=0)] = None
    route_id: str | None = None
    route_number: str | None = None
    selected: bool = False
    reason_codes: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def feasible_alternative_needs_duration(self) -> RouteAlternativeSummary:
        if self.status == "feasible" and self.duration_minutes is None:
            raise ValueError("feasible route alternative requires duration")
        return self


class TransportSelectionSummary(ContractModel):
    """추천 전체의 선택 수단과 버스 탈락 근거 집계."""

    selected_walk_legs: Annotated[int, Field(ge=0)]
    selected_bus_legs: Annotated[int, Field(ge=0)]
    selected_taxi_legs: Annotated[int, Field(ge=0)]
    feasible_bus_alternatives: Annotated[int, Field(ge=0)]
    unverifiable_bus_alternatives: Annotated[int, Field(ge=0)]
    near_threshold_legs: tuple[str, ...] = ()
    bus_rejection_counts: dict[ModeReasonCode, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict
    )


class Transfer(ContractModel):
    mode: Literal["walk", "bus", "taxi"]
    direct_walk: WalkConnection | None = None
    access_walk: WalkConnection | None = None
    bus_rides: tuple[BusRide, ...] = ()
    transfer_walks: tuple[WalkConnection, ...] = ()
    egress_walk: WalkConnection | None = None
    taxi_alternative: TaxiAlternative | None = None
    alternatives: tuple[RouteAlternativeSummary, ...] = ()
    distance_meters: Annotated[int, Field(ge=0)] = 0
    distance_is_estimated: bool = False
    mode_decision: ModeDecision | None = None

    @model_validator(mode="after")
    def bus_requires_endpoint_walks(self) -> Transfer:
        if self.mode == "bus":
            if self.access_walk is None or self.access_walk.kind != "access_walk":
                raise ValueError("bus transfer requires access_walk")
            if self.egress_walk is None or self.egress_walk.kind != "egress_walk":
                raise ValueError("bus transfer requires egress_walk")
            if not self.bus_rides:
                raise ValueError("bus transfer requires at least one bus ride")
        if self.mode == "walk" and (
            self.direct_walk is None or self.direct_walk.kind != "direct_walk"
        ):
            raise ValueError("walk transfer requires direct_walk")
        if self.mode == "taxi" and self.taxi_alternative is None:
            raise ValueError("taxi transfer requires estimated fare range")
        return self


class CostRange(ContractModel):
    min_krw: Annotated[int, Field(ge=0)]
    max_krw: Annotated[int, Field(ge=0)]
    is_estimated: bool

    @model_validator(mode="after")
    def validate_range(self) -> CostRange:
        if self.max_krw < self.min_krw:
            raise ValueError("maximum cost must be at least minimum cost")
        return self


class VisitDetails(ContractModel):
    place_id: str
    name: str
    position: Coordinates
    entrance_id: str
    arrival_at: datetime
    entry_at: datetime
    departure_at: datetime
    stay_minutes: Annotated[int, Field(gt=0)]
    operating_hours_status: Literal["VERIFIED", "UNVERIFIED"]
    opening_hours_conflict: Literal[False] = False
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    last_admission_at: datetime | None = None
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operating_hours_evidence(self) -> VisitDetails:
        if self.operating_hours_status == "VERIFIED" and (
            self.opens_at is None or self.closes_at is None
        ):
            raise ValueError("verified operating hours require opening and closing times")
        if self.operating_hours_status == "UNVERIFIED" and any(
            value is not None for value in (self.opens_at, self.closes_at, self.last_admission_at)
        ):
            raise ValueError("unverified operating hours must not contain inferred times")
        return self


class RestDetails(ContractModel):
    place_id: str
    venue_name: str | None = None
    position: Coordinates | None = None
    entrance_id: str | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    operating_hours_status: Literal["VERIFIED", "UNVERIFIED"] = "VERIFIED"
    seat: Literal["AVAILABLE", "UNKNOWN"]
    restroom: Literal["AVAILABLE", "UNKNOWN"]
    indoor: Literal["AVAILABLE", "UNKNOWN"]
    guarantee: Literal["not_guaranteed"] = "not_guaranteed"
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)


class MealDetails(ContractModel):
    place_id: str
    venue_name: str
    position: Coordinates | None = None
    entrance_id: str | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    last_order_at: datetime | None = None
    operating_hours_status: Literal["VERIFIED", "UNVERIFIED"] = "VERIFIED"
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)


class TimelineEvent(ContractModel):
    event_id: str
    sequence: Annotated[int, Field(gt=0)]
    type: Literal["visit", "transfer", "rest", "meal", "buffer"]
    start_at: datetime
    end_at: datetime
    duration_minutes: Annotated[int, Field(ge=0)]
    title: str
    place_id: str | None = None
    visit: VisitDetails | None = None
    transfer: Transfer | None = None
    rest: RestDetails | None = None
    meal: MealDetails | None = None
    reason_code: str | None = None
    issue_codes: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_duration_and_type(self) -> TimelineEvent:
        actual = int((self.end_at - self.start_at).total_seconds() // 60)
        if actual != self.duration_minutes:
            raise ValueError("event duration does not match start/end")
        if self.type == "transfer" and self.transfer is None:
            raise ValueError("transfer event requires transfer details")
        if self.type != "transfer" and self.transfer is not None:
            raise ValueError("only transfer events may contain transfer details")
        if (self.type == "visit") != (self.visit is not None):
            raise ValueError("visit details must appear only on visit events")
        if (self.type == "rest") != (self.rest is not None):
            raise ValueError("rest details must appear only on rest events")
        if (self.type == "meal") != (self.meal is not None):
            raise ValueError("meal details must appear only on meal events")
        return self


class Totals(ContractModel):
    total_minutes: Annotated[int, Field(ge=0)]
    visit_minutes: Annotated[int, Field(ge=0)]
    transfer_minutes: Annotated[int, Field(ge=0)]
    rest_minutes: Annotated[int, Field(ge=0)]
    meal_minutes: Annotated[int, Field(ge=0)]
    buffer_minutes: Annotated[int, Field(ge=0)]
    walking_minutes: Annotated[int, Field(ge=0)]
    walking_distance_meters: Annotated[int, Field(ge=0)]
    taxi_pickup_buffer_minutes: Annotated[int, Field(ge=0)] = 0
    bus_wait_minutes: Annotated[int, Field(ge=0)] = 0
    estimated_cost: CostRange
    bus_distance_meters: Annotated[int, Field(ge=0)] = 0
    taxi_distance_meters: Annotated[int, Field(ge=0)] = 0
    total_distance_meters: Annotated[int, Field(ge=0)] = 0
    bus_cost: CostRange = Field(
        default_factory=lambda: CostRange(min_krw=0, max_krw=0, is_estimated=False)
    )
    taxi_cost: CostRange = Field(
        default_factory=lambda: CostRange(min_krw=0, max_krw=0, is_estimated=False)
    )
    derivation_evidence_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_breakdowns(self) -> Totals:
        if self.total_distance_meters != (
            self.walking_distance_meters + self.bus_distance_meters + self.taxi_distance_meters
        ):
            raise ValueError("distance breakdown does not match total")
        minimum = sum(
            item.min_krw
            for item in (
                self.bus_cost,
                self.taxi_cost,
            )
        )
        maximum = sum(
            item.max_krw
            for item in (
                self.bus_cost,
                self.taxi_cost,
            )
        )
        if (minimum, maximum) != (self.estimated_cost.min_krw, self.estimated_cost.max_krw):
            raise ValueError("cost breakdown does not match total")
        return self


class GroundedReason(ContractModel):
    text: str
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)


class RecommendationSegmentRisk(ContractModel):
    event_id: str
    risk: Literal["critical", "high", "medium", "low", "unknown"]
    slack_minutes: int | None = None
    reason_codes: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()


class ScoreComponent(ContractModel):
    value: Annotated[float, Field(ge=0, le=100)]
    weight: Annotated[int, Field(ge=0, le=100)]
    weighted_value: Annotated[float, Field(ge=0, le=100)]
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)


ScoreComponentName = Literal[
    "preferred_places",
    "travel_efficiency",
    "reliability",
    "comfort",
    "cost_efficiency",
    "data_confidence",
]


class RecommendationScore(ContractModel):
    total: Annotated[float, Field(ge=0, le=100)]
    components: dict[ScoreComponentName, ScoreComponent]

    @model_validator(mode="after")
    def validate_weighted_total(self) -> RecommendationScore:
        expected = round(sum(item.weighted_value for item in self.components.values()), 2)
        if round(self.total, 2) != expected:
            raise ValueError("recommendation score total does not match components")
        if sum(item.weight for item in self.components.values()) != 100:
            raise ValueError("recommendation score weights must total 100")
        return self


class Strategy(StrEnum):
    BALANCED = "balanced"
    RELAXED = "relaxed"
    EXPERIENCE_MAX = "experience_max"


class Recommendation(ContractModel):
    route_id: str
    rank: Annotated[int, Field(ge=1, le=3)]
    strategy: Strategy
    title: str
    score: RecommendationScore
    recommendation_reasons: tuple[GroundedReason, ...] = Field(min_length=1)
    tradeoffs: tuple[str, ...] = ()
    place_ids: tuple[str, ...] = Field(min_length=1)
    feasibility: Literal["feasible", "feasible_with_caution"]
    overall_risk: Literal["high", "medium", "low"] = "low"
    segment_risks: tuple[RecommendationSegmentRisk, ...] = ()
    revalidate_at: tuple[datetime, ...] = ()
    required_place_ids_included: tuple[str, ...] = ()
    preferred_place_ids_included: tuple[str, ...] = ()
    preferred_place_ids_excluded: tuple[str, ...] = ()
    day_start_at: datetime
    day_end_at: datetime
    start_place_id: Annotated[str, Field(min_length=1)]
    end_place_id: Annotated[str, Field(min_length=1)]
    accommodation_departure_at: datetime
    accommodation_return_at: datetime
    timeline: tuple[TimelineEvent, ...] = Field(min_length=1)
    totals: Totals
    transport_selection_summary: TransportSelectionSummary
    issue_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_timeline(self) -> Recommendation:
        ordered = sorted(self.timeline, key=lambda event: event.sequence)
        if list(self.timeline) != ordered:
            raise ValueError("timeline must be ordered by sequence")
        if len({event.sequence for event in self.timeline}) != len(self.timeline):
            raise ValueError("timeline sequences must be unique")
        for previous, current in zip(self.timeline, self.timeline[1:], strict=False):
            if previous.end_at > current.start_at:
                raise ValueError("timeline events must not overlap")
        if self.accommodation_departure_at != self.timeline[0].start_at:
            raise ValueError("accommodation departure must match first timeline event")
        if self.accommodation_return_at != self.timeline[-1].end_at:
            raise ValueError("accommodation return must match last timeline event")
        if self.accommodation_return_at <= self.accommodation_departure_at:
            raise ValueError("accommodation return must follow departure")
        if self.day_start_at != self.timeline[0].start_at:
            raise ValueError("day start must match first timeline event")
        if self.day_end_at != self.timeline[-1].end_at:
            raise ValueError("day end must match last timeline event")
        if self.day_end_at <= self.day_start_at:
            raise ValueError("day end must follow day start")
        for event in self.timeline:
            if event.visit is None:
                continue
            if (
                event.visit.arrival_at != event.start_at
                or event.visit.entry_at != event.start_at
                or event.visit.departure_at != event.end_at
                or event.visit.stay_minutes != event.duration_minutes
            ):
                raise ValueError("visit timing details must match timeline event")
        required_event_types = {"visit", "transfer"}
        if not required_event_types.issubset({event.type for event in self.timeline}):
            raise ValueError("recommendation requires visit, transfer, rest, and meal events")
        total_minutes = sum(event.duration_minutes for event in self.timeline)
        if total_minutes != self.totals.total_minutes:
            raise ValueError("timeline total does not match totals")
        by_type = {
            event_type: sum(
                event.duration_minutes for event in self.timeline if event.type == event_type
            )
            for event_type in ("visit", "transfer", "rest", "meal", "buffer")
        }
        for field, event_type in (
            ("visit_minutes", "visit"),
            ("transfer_minutes", "transfer"),
            ("rest_minutes", "rest"),
            ("meal_minutes", "meal"),
            ("buffer_minutes", "buffer"),
        ):
            if getattr(self.totals, field) != by_type[event_type]:
                raise ValueError(f"{field} does not match timeline")
        return self


def recommendations_are_materially_different(first: Recommendation, second: Recommendation) -> bool:
    first_places = set(first.place_ids)
    second_places = set(second.place_ids)
    union = first_places | second_places
    jaccard = len(first_places & second_places) / len(union) if union else 1.0
    longest = max(len(first.place_ids), len(second.place_ids), 1)
    same_positions = sum(
        left == right for left, right in zip(first.place_ids, second.place_ids, strict=False)
    )
    order_similarity = same_positions / longest
    first_modes = tuple(
        event.transfer.mode
        for event in first.timeline
        if event.transfer is not None and event.transfer.mode != "walk"
    )
    second_modes = tuple(
        event.transfer.mode
        for event in second.timeline
        if event.transfer is not None and event.transfer.mode != "walk"
    )
    mode_count = max(len(first_modes), len(second_modes))
    if mode_count:
        same_modes = sum(
            left == right for left, right in zip(first_modes, second_modes, strict=False)
        )
        different_mode_ratio = 1 - same_modes / mode_count
    else:
        different_mode_ratio = 0.0
    first_stays = tuple(
        event.duration_minutes
        for event in first.timeline
        if event.type in {"visit", "meal", "rest"}
    )
    second_stays = tuple(
        event.duration_minutes
        for event in second.timeline
        if event.type in {"visit", "meal", "rest"}
    )
    return (
        jaccard <= 0.8
        or order_similarity <= 0.7
        or different_mode_ratio >= 0.25
        or first_stays != second_stays
    )


class PlanningContext(ContractModel):
    mode: Literal["advance_planning"] = "advance_planning"
    planned_at: datetime
    trip_date: date
    days_before_trip: Annotated[int, Field(ge=0)]
    schedule_basis: Literal["future_timetable", "service_calendar"]
    realtime_used: Literal[False] = False
    plan_expires_at: datetime
    revalidate_at: tuple[datetime, ...] = ()


class ValidationSummary(ContractModel):
    schema_valid: bool
    timeline_valid: bool
    provenance_valid: bool
    transit_connections_valid: bool
    diversity_valid: bool
    checks: tuple[str, ...] = ()


class Failure(ContractModel):
    code: Literal[
        "insufficient_feasible_routes",
        "DATA_NOT_READY",
        "ENTRANCE_UNVERIFIED",
        "STOP_MAPPING_UNCONFIRMED",
        "SOURCE_PENDING",
        "PLACE_AMBIGUOUS",
        "PLANNING_TIMEOUT",
        "ROUTING_BUDGET_EXHAUSTED",
        "REGION_SCOPE_NOT_READY",
        "DATE_SCOPE_NOT_READY",
        "BUS_SERVICE_DAY_UNVERIFIED",
        "REQUEST_MODE_REMOVED",
    ]
    message: str
    reason_codes: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()


class PlaceDecision(ContractModel):
    place_id: str | None
    requested_priority: Literal["required", "preferred", "excluded"]
    decision: Literal["included", "excluded", "unresolved", "unverifiable"]
    reason_codes: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()


class CapabilityCoverage(ContractModel):
    capability: str
    ready: bool
    blocking_reason: str | None = None
    ratio: Annotated[float, Field(ge=0, le=1)] = 0
    region_code: str = "JEJU_ALL"
    grid_id: str = "ALL"
    service_date_from: date | None = None
    service_date_to: date | None = None
    source_refs: tuple[SourceRef, ...] = ()
    measured_at: datetime | None = None
    checked_at: datetime | None = None


class DayTripResponse(ContractModel):
    schema_version: Literal["0.7.0"] = SCHEMA_VERSION
    request_id: str
    generated_at: datetime
    status: Literal["success", "insufficient_feasible_routes"]
    planning_context: PlanningContext
    request: RecommendDayTripsInput
    assumptions: tuple[Assumption, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()
    evidence_facts: tuple[EvidenceFact, ...] = ()
    data_sources: tuple[DataSourceMetadata, ...] = ()
    place_decisions: tuple[PlaceDecision, ...] = ()
    capability_coverage: tuple[CapabilityCoverage, ...] = ()
    global_warnings: tuple[str, ...] = ()
    validation: ValidationSummary
    failure: Failure | None = None

    @model_validator(mode="after")
    def validate_whole_response(self) -> DayTripResponse:
        if self.status == "success":
            if len(self.recommendations) != 3:
                raise ValueError("success requires exactly three recommendations")
            if {item.strategy for item in self.recommendations} != set(Strategy):
                raise ValueError("success requires one recommendation for each strategy")
            if any(
                not recommendations_are_materially_different(first, second)
                for index, first in enumerate(self.recommendations)
                for second in self.recommendations[index + 1 :]
            ):
                raise ValueError("success recommendations must be diverse")
            required_ids = {
                item.place_id for item in self.request.required_places if item.place_id is not None
            }
            if any(
                not required_ids.issubset(recommendation.place_ids)
                for recommendation in self.recommendations
            ):
                raise ValueError("success recommendations must include every required place")
            if self.failure is not None:
                raise ValueError("success must not include failure")
            if self.request.food.auto_schedule_meals and any(
                not any(event.type == "meal" and event.meal is not None for event in item.timeline)
                for item in self.recommendations
            ):
                raise ValueError("auto meal scheduling requires a named meal venue")
            if self.request.food.auto_schedule_cafe and any(
                not any(event.type == "rest" and event.rest is not None for event in item.timeline)
                for item in self.recommendations
            ):
                raise ValueError("auto cafe scheduling requires a named rest venue")
        else:
            if self.recommendations:
                raise ValueError("failure must not include recommendations")
            if self.failure is None:
                raise ValueError("failure response requires failure details")

        _validate_evidence_closure(
            self.evidence_facts,
            (self.recommendations, self.place_decisions),
        )
        _validate_source_lineage(self.evidence_facts, self.data_sources)
        return self


class SearchPlacesInput(ContractModel):
    query: Annotated[str, Field(min_length=1)]
    limit: Annotated[int, Field(ge=1, le=50)] = 10


class PlaceSummary(ContractModel):
    place_id: str
    name: str
    category: str
    address: str
    position: Coordinates
    verified_entrance_count: Annotated[int, Field(ge=0)]
    source_refs: tuple[SourceRef, ...]


class SearchPlacesResponse(ContractModel):
    status: Literal["success", "data_unavailable"]
    places: tuple[PlaceSummary, ...] = ()
    reason_code: str | None = None


class InspectBusStopInput(ContractModel):
    stop_id: str


class BusStopInspection(ContractModel):
    status: Literal["success", "not_found", "data_unavailable"]
    canonical_stop_id: str | None = None
    provider_stop_id: str | None = None
    name: str | None = None
    direction_text: str | None = None
    position: Coordinates | None = None
    mapping_method: (
        Literal["OFFICIAL_ID", "COORDINATE_AND_NAME", "ROUTE_SEQUENCE", "CURATED"] | None
    ) = None
    mapping_status: Literal["CONFIRMED", "REVIEW_REQUIRED", "REJECTED"] | None = None
    route_numbers: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()
    reason_code: str | None = None


class PreviewTransferInput(ContractModel):
    origin_place_id: str
    destination_place_id: str
    departure_at: datetime
    allowed_modes: set[Literal["walk", "bus", "taxi"]] = Field(
        default_factory=lambda: {"walk", "bus"}
    )

    @model_validator(mode="after")
    def validate_preview(self) -> PreviewTransferInput:
        _require_kst(self.departure_at, "departure_at")
        if not self.allowed_modes:
            raise ValueError("at least one allowed mode is required")
        return self


class PreviewTransferResponse(ContractModel):
    status: Literal["success", "unavailable"]
    transfer: Transfer | None = None
    evidence_facts: tuple[EvidenceFact, ...] = ()
    data_sources: tuple[DataSourceMetadata, ...] = ()
    reason_code: str | None = None

    @model_validator(mode="after")
    def validate_evidence_closure(self) -> PreviewTransferResponse:
        _validate_evidence_closure(self.evidence_facts, (self.transfer,))
        _validate_source_lineage(self.evidence_facts, self.data_sources)
        return self


class CommonTripInput(BoundaryTripInput):
    """생성·판정이 공유하는 하루 시작·종료 경계 여행 조건."""

    schema_version: Literal["0.7.0"] = SCHEMA_VERSION
    trip_date: date
    timezone: Literal["Asia/Seoul"] = KST_NAME
    party: Party = Field(default_factory=Party)
    transport: TransportPreferences = Field(default_factory=TransportPreferences)
    walking: WalkingPreferences = Field(default_factory=WalkingPreferences)
    rest: RestPreferences = Field(default_factory=RestPreferences)
    food: FoodPreferences = Field(default_factory=FoodPreferences)
    total_budget_krw: Annotated[int | None, Field(ge=0)] = None

    @model_validator(mode="after")
    def validate_common_time(self) -> CommonTripInput:
        if self.activity_window.start_at.date() != self.trip_date:
            raise ValueError("activity_window.start_at must use trip_date")
        return self


class ScheduledActivity(ContractModel):
    event_id: Annotated[str, Field(min_length=1)]
    type: Literal["visit", "meal", "rest"]
    place: PlaceReference
    start_at: datetime
    end_at: datetime
    required: bool = False

    @model_validator(mode="after")
    def validate_activity(self) -> ScheduledActivity:
        _require_kst(self.start_at, "scheduled activity start_at")
        _require_kst(self.end_at, "scheduled activity end_at")
        if self.end_at <= self.start_at:
            raise ValueError("scheduled activity end must follow start")
        return self


class LegConstraint(ContractModel):
    from_event_id: str | None
    to_event_id: str | None
    locked_mode: Literal["walk", "bus", "taxi"] | None = None

    @model_validator(mode="after")
    def require_event_boundary(self) -> LegConstraint:
        if self.from_event_id is None and self.to_event_id is None:
            raise ValueError("at least one leg boundary event is required")
        return self


class FullTimelineActivity(ContractModel):
    event_id: Annotated[str, Field(min_length=1)]
    type: Literal["visit", "meal", "rest"]
    start_at: datetime
    end_at: datetime
    place: PlaceReference
    required: bool = False

    @model_validator(mode="after")
    def validate_activity(self) -> FullTimelineActivity:
        _require_kst(self.start_at, "timeline activity start_at")
        _require_kst(self.end_at, "timeline activity end_at")
        if self.end_at <= self.start_at:
            raise ValueError("timeline activity end must follow start")
        return self


class FullTimelineTransfer(ContractModel):
    event_id: Annotated[str, Field(min_length=1)]
    type: Literal["transfer"]
    start_at: datetime
    end_at: datetime
    from_place: PlaceReference
    to_place: PlaceReference
    planned_mode: Literal["walk", "bus", "taxi"]
    planned_route_number: str | None = None
    planned_route_id: str | None = None
    planned_boarding_stop_id: str | None = None
    planned_alighting_stop_id: str | None = None
    scheduled_departure_at: datetime | None = None
    scheduled_arrival_at: datetime | None = None
    planned_distance_meters: Annotated[int | None, Field(ge=0)] = None
    route_alternatives: tuple[RouteAlternativeSummary, ...] = ()
    mode_decision: ModeDecision | None = None
    selected_transfer: Transfer | None = None
    evidence_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_transfer(self) -> FullTimelineTransfer:
        _require_kst(self.start_at, "timeline transfer start_at")
        _require_kst(self.end_at, "timeline transfer end_at")
        if self.end_at <= self.start_at:
            raise ValueError("timeline transfer end must follow start")
        bus_claims = (
            self.planned_route_id,
            self.planned_route_number,
            self.planned_boarding_stop_id,
            self.planned_alighting_stop_id,
            self.scheduled_departure_at,
            self.scheduled_arrival_at,
        )
        if self.planned_mode == "bus" and any(item is None for item in bus_claims):
            raise ValueError("bus transfer requires complete stop, route, and schedule claims")
        if self.planned_mode != "bus" and any(item is not None for item in bus_claims):
            raise ValueError("bus claims are only allowed for bus transfer")
        if self.selected_transfer is not None and self.selected_transfer.mode != self.planned_mode:
            raise ValueError("selected transfer mode must match planned_mode")
        return self


class FullTimelineBuffer(ContractModel):
    event_id: Annotated[str, Field(min_length=1)]
    type: Literal["buffer"]
    start_at: datetime
    end_at: datetime
    place: PlaceReference | None = None
    reason_code: str
    evidence_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_buffer(self) -> FullTimelineBuffer:
        _require_kst(self.start_at, "timeline buffer start_at")
        _require_kst(self.end_at, "timeline buffer end_at")
        if self.end_at <= self.start_at:
            raise ValueError("timeline buffer end must follow start")
        return self


FullTimelineItem = Annotated[
    FullTimelineActivity | FullTimelineTransfer | FullTimelineBuffer, Field(discriminator="type")
]


class ActivitiesOnlyEvaluationInput(CommonTripInput):
    schedule_format: Literal["activities_only"]
    start_location: PlaceReference | None = None
    scheduled_activities: tuple[ScheduledActivity, ...]
    leg_constraints: tuple[LegConstraint, ...] = ()


class FullTimelineEvaluationInput(CommonTripInput):
    schedule_format: Literal["full_timeline"]
    timeline: tuple[FullTimelineItem, ...]


EvaluateJejuDayTripInput = Annotated[
    ActivitiesOnlyEvaluationInput | FullTimelineEvaluationInput,
    Field(discriminator="schedule_format"),
]


class NormalizedScheduleEvent(ContractModel):
    event_id: str
    type: Literal["visit", "meal", "rest", "transfer", "buffer", "unplanned_gap"]
    start_at: datetime
    end_at: datetime
    from_place_id: str | None = None
    to_place_id: str | None = None
    place_id: str | None = None
    mode: Literal["walk", "bus", "taxi"] | None = None
    reason_code: str | None = None
    source_event_id: str | None = None


class NormalizedSchedule(ContractModel):
    events: tuple[NormalizedScheduleEvent, ...]


class EvaluationIssue(ContractModel):
    issue_id: str
    category: Literal[
        "timeline",
        "opening_hours",
        "transit_connection",
        "walking",
        "accessibility",
        "budget",
        "hotel_return",
        "meal_rest",
        "data_freshness",
        "realtime_progress",
    ]
    severity: Literal["critical", "high", "medium", "low", "unknown"]
    event_ids: tuple[str, ...] = ()
    reason_code: str
    slack_minutes: int | None = None
    message: str
    evidence_fact_ids: tuple[str, ...] = ()


class SegmentEvaluation(ContractModel):
    segment_id: str
    from_event_id: str | None
    to_event_id: str | None
    mode: Literal["walk", "bus", "taxi"] | None
    status: Literal["feasible", "infeasible", "unverifiable"]
    risk: Literal["critical", "high", "medium", "low", "unknown"]
    available_minutes: int
    required_minutes: int | None
    slack_minutes: int | None
    walking_minutes: Annotated[int, Field(ge=0)] = 0
    walking_distance_meters: Annotated[int, Field(ge=0)] = 0
    distance_meters: Annotated[int | None, Field(ge=0)] = None
    cost_krw: Annotated[int, Field(ge=0)] = 0
    bus_wait_minutes: Annotated[int, Field(ge=0)] = 0
    transfers: Annotated[int, Field(ge=0)] = 0
    evidence_fact_ids: tuple[str, ...] = ()
    cost: CostRange = Field(
        default_factory=lambda: CostRange(min_krw=0, max_krw=0, is_estimated=False)
    )
    reason_codes: tuple[str, ...] = ()


class ActivityEvaluation(ContractModel):
    event_id: str
    place_id: str
    operating_hours_status: Literal["VERIFIED_OPEN", "CONFLICT", "UNVERIFIABLE"]
    reason_codes: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()


class RepairChange(ContractModel):
    event_id: str
    field: str
    before: Any
    after: Any


class RepairOption(ContractModel):
    repair_id: str
    repair_type: Literal[
        "DEPART_EARLIER",
        "USE_FASTER_VERIFIED_ALTERNATIVE",
        "SHORTEN_STAY",
        "SKIP_OPTIONAL_ACTIVITY",
        "TAXI_AFTER_BUS_TIMEOUT",
    ]
    changes: tuple[RepairChange, ...]
    result_if_applied: Literal["feasible", "feasible_with_caution"]
    new_overall_risk: Literal["high", "medium", "low"]
    tradeoffs: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = Field(min_length=1)
    time_gained_minutes: Annotated[int, Field(ge=0)] = 0
    cost_increase: CostRange = Field(
        default_factory=lambda: CostRange(min_krw=0, max_krw=0, is_estimated=False)
    )
    changed_activity_ids: tuple[str, ...] = ()
    new_evidence_fact_ids: tuple[str, ...] = ()
    revalidated_evaluation: EvaluationResponse | None = None


class EvaluationTotals(ContractModel):
    """전체 타임라인에서 재계산한 판정용 합계."""

    walking_distance_meters: Annotated[int, Field(ge=0)] = 0
    total_distance_meters: Annotated[int, Field(ge=0)] = 0
    activity_minutes: Annotated[int, Field(ge=0)] = 0
    transfer_minutes: Annotated[int, Field(ge=0)] = 0
    taxi_pickup_buffer_minutes: Annotated[int, Field(ge=0)] = 0
    bus_wait_minutes: Annotated[int, Field(ge=0)] = 0
    transport_cost: CostRange = Field(
        default_factory=lambda: CostRange(min_krw=0, max_krw=0, is_estimated=False)
    )
    return_slack_minutes: int | None = None
    mismatch_fields: tuple[str, ...] = ()


class EvaluationResponse(ContractModel):
    schema_version: Literal["0.7.0"] = SCHEMA_VERSION
    status: Literal["feasible", "feasible_with_caution", "infeasible", "unverifiable"]
    timing_status: Literal["on_schedule", "at_risk", "disrupted", "unknown"]
    evidence_status: Literal["verified", "partial", "unavailable"]
    overall_risk: Literal["critical", "high", "medium", "low", "unknown"]
    schedule_window_fit: bool = False
    normalized_schedule: NormalizedSchedule
    issues: tuple[EvaluationIssue, ...] = ()
    segment_evaluations: tuple[SegmentEvaluation, ...] = ()
    activity_evaluations: tuple[ActivityEvaluation, ...] = ()
    repair_options: tuple[RepairOption, ...] = ()
    evidence_facts: tuple[EvidenceFact, ...] = ()
    data_sources: tuple[DataSourceMetadata, ...] = ()
    validation: ValidationSummary
    failure: Failure | None = None
    total_distance_meters: Annotated[int, Field(ge=0)] = 0
    total_cost: CostRange = Field(
        default_factory=lambda: CostRange(min_krw=0, max_krw=0, is_estimated=False)
    )
    totals: EvaluationTotals = Field(default_factory=EvaluationTotals)

    @model_validator(mode="after")
    def validate_failure_status(self) -> EvaluationResponse:
        if self.status == "unverifiable" and self.failure is None:
            raise ValueError("unverifiable evaluation requires failure")
        if self.status != "unverifiable" and self.failure is not None:
            raise ValueError("only unverifiable evaluation may include failure")
        _validate_evidence_closure(
            self.evidence_facts,
            (
                self.issues,
                self.segment_evaluations,
                self.activity_evaluations,
                self.repair_options,
            ),
        )
        _validate_source_lineage(self.evidence_facts, self.data_sources)
        return self


class ProgressInput(ContractModel):
    state: Literal["at_place", "ready_to_depart", "walking", "waiting_bus", "on_bus", "in_taxi"]
    current_event_id: str
    completed_event_ids: tuple[str, ...] = ()
    actual_time: datetime
    current_place_id: str | None = None
    current_stop_id: str | None = None
    current_route_id: str | None = None
    current_event_started_at: datetime | None = None

    @model_validator(mode="after")
    def validate_actual_time(self) -> ProgressInput:
        _require_kst(self.actual_time, "progress.actual_time")
        if self.current_event_started_at is not None:
            _require_kst(self.current_event_started_at, "progress.current_event_started_at")
            if self.current_event_started_at > self.actual_time:
                raise ValueError("current_event_started_at must not follow actual_time")
        if self.state == "at_place" and self.current_event_started_at is None:
            raise ValueError("at_place requires current_event_started_at")
        if self.state in {"waiting_bus", "on_bus"} and (
            not self.current_stop_id or not self.current_route_id
        ):
            raise ValueError("waiting_bus/on_bus require current_stop_id and current_route_id")
        if self.state in {"at_place", "ready_to_depart"} and not self.current_place_id:
            raise ValueError("at_place/ready_to_depart require current_place_id")
        if len(self.completed_event_ids) != len(set(self.completed_event_ids)):
            raise ValueError("completed_event_ids must not contain duplicates")
        if self.current_event_id in self.completed_event_ids:
            raise ValueError("current event cannot already be completed")
        return self


class RevalidateJejuDayTripInput(ContractModel):
    schema_version: Literal["0.7.0"] = SCHEMA_VERSION
    checked_at: datetime
    current_position: Coordinates | None = None
    progress: ProgressInput
    itinerary: EvaluateJejuDayTripInput

    @model_validator(mode="after")
    def validate_checked_at(self) -> RevalidateJejuDayTripInput:
        _require_kst(self.checked_at, "checked_at")
        if self.checked_at != self.progress.actual_time:
            raise ValueError("checked_at must equal progress.actual_time")
        return self


class RecoveryOption(ContractModel):
    recovery_id: str
    action: Literal[
        "WAIT_BUS",
        "TAKE_TAXI",
        "SHORTEN_STAY",
        "SKIP_PREFERRED",
        "TAXI_AFTER_BUS_TIMEOUT",
    ]
    affected_event_ids: tuple[str, ...]
    result_status: Literal["on_schedule", "at_risk"]
    evidence_fact_ids: tuple[str, ...] = ()
    replacement_transfer: FullTimelineTransfer | None = None
    expected_duration_minutes: Annotated[int | None, Field(gt=0)] = None
    expected_distance_meters: Annotated[int | None, Field(ge=0)] = None
    expected_cost: CostRange | None = None
    reason_codes: tuple[str, ...] = ()
    revalidated_evaluation: EvaluationResponse | None = None


class RevalidationResponse(ContractModel):
    schema_version: Literal["0.7.0"] = SCHEMA_VERSION
    status: Literal["on_schedule", "at_risk", "disrupted", "data_unavailable"]
    timing_status: Literal["on_schedule", "at_risk", "disrupted", "unknown"]
    evidence_status: Literal["verified", "partial", "unavailable"]
    checked_at: datetime
    delay_minutes: Annotated[int, Field(ge=0)]
    location_basis: Literal["gps", "event", "unverifiable"]
    original_evaluation: EvaluationResponse
    remaining_evaluation: EvaluationResponse | None = None
    recovery_options: tuple[RecoveryOption, ...] = ()
    warnings: tuple[str, ...] = ()
    failure: Failure | None = None

    @model_validator(mode="after")
    def validate_failure_status(self) -> RevalidationResponse:
        if self.status == "data_unavailable" and self.failure is None:
            raise ValueError("data_unavailable revalidation requires failure")
        if self.status != "data_unavailable" and self.failure is not None:
            raise ValueError("only data_unavailable revalidation may include failure")
        nested_evaluations = tuple(
            evaluation
            for evaluation in (
                self.original_evaluation,
                self.remaining_evaluation,
                *(option.revalidated_evaluation for option in self.recovery_options),
            )
            if evaluation is not None
        )
        evidence_facts = tuple(
            fact for evaluation in nested_evaluations for fact in evaluation.evidence_facts
        )
        _validate_evidence_closure(
            evidence_facts,
            (self.recovery_options,),
            allow_duplicate_fact_ids=True,
        )
        return self


# 순환 공개 계약(previous day recommendation, verified repair)을 모두 선언한 뒤 해석한다.
SelectedDayHistory.model_rebuild()
RecommendDayTripsInput.model_rebuild()
RepairOption.model_rebuild()
EvaluationResponse.model_rebuild()
