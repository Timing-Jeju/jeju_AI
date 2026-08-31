"""LangGraph에서 허용된 LLM 단계와 결정론적 단계를 분리한다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph


class PlannerGraphState(TypedDict):
    structured_request: dict[str, object]
    parsed_original: NotRequired[dict[str, object]]
    known_fact_ids: set[str]
    candidate_orders: NotRequired[list[list[str]]]
    validated_candidates: NotRequired[list[dict[str, object]]]
    reasons: NotRequired[list[dict[str, object]]]
    failure_code: NotRequired[str]


def build_planner_graph(
    parse_original: Callable[[PlannerGraphState], dict[str, object]],
    generate_orders: Callable[[PlannerGraphState], dict[str, object]],
    deterministic_schedule_and_validate: Callable[[PlannerGraphState], dict[str, object]],
    write_grounded_reasons: Callable[[PlannerGraphState], dict[str, object]],
):
    """LLM은 parse/order/reason 노드에만 주입하고 계산 노드는 별도로 고정한다."""

    graph = StateGraph(PlannerGraphState)
    graph.add_node("parse_original", cast(Any, parse_original))
    graph.add_node("generate_orders", cast(Any, generate_orders))
    graph.add_node(
        "deterministic_schedule_and_validate",
        cast(Any, deterministic_schedule_and_validate),
    )
    graph.add_node("write_grounded_reasons", cast(Any, write_grounded_reasons))
    graph.add_edge(START, "parse_original")
    graph.add_edge("parse_original", "generate_orders")
    graph.add_edge("generate_orders", "deterministic_schedule_and_validate")
    graph.add_edge("deterministic_schedule_and_validate", "write_grounded_reasons")
    graph.add_edge("write_grounded_reasons", END)
    return graph.compile()
