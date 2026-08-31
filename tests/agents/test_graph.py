"""LangGraph 책임 분리 연결 테스트."""

from __future__ import annotations

from jeju_trip.agents.graph import PlannerGraphState, build_planner_graph


def test_graph_runs_llm_boundaries_around_deterministic_validation() -> None:
    """그래프는 원문·후보·설명 노드 사이에 결정론적 검증 노드를 실행해야 한다."""

    calls: list[str] = []

    def node(name: str):
        def run(state: PlannerGraphState) -> dict[str, object]:
            calls.append(name)
            return {}

        return run

    graph = build_planner_graph(
        node("parse"), node("orders"), node("deterministic"), node("reasons")
    )
    graph.invoke({"structured_request": {}, "known_fact_ids": set()})
    assert calls == ["parse", "orders", "deterministic", "reasons"]
