"""LangGraph orchestration for the grounded recommendation pipeline.

Wraps existing stage logic without changing retrieval or validation rules.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agent_graph import RecommendationGraphState, log_graph_state
from app.observability import event_logger

logger = event_logger("skillorbit.langgraph")


class PipelineState(TypedDict, total=False):
    access_token: str
    user_id: str
    learner: dict[str, Any] | None
    graph: RecommendationGraphState
    profile: dict[str, Any]
    query: str
    matches: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    relevant_candidates: list[dict[str, Any]]
    narrative: dict[str, Any]
    summary: str
    next_step: str
    safe_items: list[dict[str, Any]]
    result: dict[str, Any]
    failed: bool


def _import_stages():
    from app.recommendations import (
        RecommendationError,
        _mesh_narrative,
        _safe_narrative,
        _insert_recommendation,
        _stage_analyze,
        _stage_retrieve,
        _stage_evaluate,
    )
    return (
        RecommendationError,
        _mesh_narrative,
        _safe_narrative,
        _insert_recommendation,
        _stage_analyze,
        _stage_retrieve,
        _stage_evaluate,
    )


async def node_analyze(state: PipelineState) -> PipelineState:
    (
        RecommendationError,
        *_,
        _stage_analyze,
        _,
        _,
    ) = _import_stages()
    graph = state["graph"]
    try:
        profile, query = await _stage_analyze(
            state["access_token"],
            state["user_id"],
            state.get("learner"),
            graph,
        )
        return {
            **state,
            "profile": profile,
            "query": query,
            "failed": False,
        }
    except RecommendationError:
        log_graph_state(logger, graph)
        return {**state, "failed": True}


async def node_retrieve(state: PipelineState) -> PipelineState:
    if state.get("failed"):
        return state
    RecommendationError, _, _, _, _, _stage_retrieve, _ = _import_stages()
    graph = state["graph"]
    try:
        matches, candidates = await _stage_retrieve(state["query"], graph)
        return {
            **state,
            "matches": matches,
            "candidates": candidates,
            "failed": False,
        }
    except RecommendationError:
        log_graph_state(logger, graph)
        return {**state, "failed": True}


async def node_evaluate(state: PipelineState) -> PipelineState:
    if state.get("failed"):
        return state
    _, _, _, _, _, _, _stage_evaluate = _import_stages()
    graph = state["graph"]
    relevant = _stage_evaluate(
        state["candidates"],
        state["matches"],
        state.get("learner"),
        state.get("profile"),
        graph,
    )
    return {**state, "relevant_candidates": relevant, "failed": False}


async def node_generate(state: PipelineState) -> PipelineState:
    if state.get("failed"):
        return state
    RecommendationError, _mesh_narrative, _, _, _, _, _ = _import_stages()
    graph = state["graph"]
    generate_started = graph.start_stage("generate")
    try:
        narrative = await _mesh_narrative(
            state["query"],
            state.get("profile"),
            state["relevant_candidates"],
        )
    except RecommendationError:
        graph.fail_stage("generate", generate_started, "narrative_unavailable")
        log_graph_state(logger, graph)
        return {**state, "failed": True}
    from app.config import settings
    graph.finish_stage("generate", generate_started, model=settings.mesh_chat_model)
    return {**state, "narrative": narrative, "failed": False}


async def node_validate(state: PipelineState) -> PipelineState:
    if state.get("failed"):
        return state
    RecommendationError, _, _safe_narrative, _, _, _, _ = _import_stages()
    graph = state["graph"]
    validate_started = graph.start_stage("validate")
    summary, next_step, safe_items = _safe_narrative(
        state["narrative"],
        state["relevant_candidates"],
    )
    scores = {match["product_id"]: match["score"] for match in state["matches"]}
    for item in safe_items:
        item["score"] = scores.get(item["product_id"], 0)
    if not safe_items:
        graph.fail_stage("validate", validate_started, "no_valid_items")
        log_graph_state(logger, graph)
        return {**state, "failed": True}
    graph.finish_stage("validate", validate_started, valid_item_count=len(safe_items))
    return {
        **state,
        "summary": summary,
        "next_step": next_step,
        "safe_items": safe_items,
        "failed": False,
    }


async def node_persist(state: PipelineState) -> PipelineState:
    if state.get("failed"):
        return state
    RecommendationError, _, _, _insert_recommendation, _, _, _ = _import_stages()
    from app.config import settings
    graph = state["graph"]
    persist_started = graph.start_stage("persist")
    try:
        result = await _insert_recommendation(
            state["access_token"],
            state["user_id"],
            state["profile"],
            state["query"],
            state["summary"],
            state["next_step"],
            state["safe_items"],
            settings.mesh_chat_model,
            graph,
        )
    except RecommendationError:
        graph.fail_stage("persist", persist_started, "history_unavailable")
        log_graph_state(logger, graph)
        return {**state, "failed": True}
    graph.finish_stage("persist", persist_started, item_count=len(state["safe_items"]))
    graph.complete()
    log_graph_state(logger, graph)
    return {**state, "result": result, "failed": False}


def build_recommendation_graph() -> Any:
    workflow = StateGraph(PipelineState)
    workflow.add_node("analyze", node_analyze)
    workflow.add_node("retrieve", node_retrieve)
    workflow.add_node("evaluate", node_evaluate)
    workflow.add_node("generate", node_generate)
    workflow.add_node("validate", node_validate)
    workflow.add_node("persist", node_persist)
    workflow.set_entry_point("analyze")
    workflow.add_edge("analyze", "retrieve")
    workflow.add_edge("retrieve", "evaluate")
    workflow.add_edge("evaluate", "generate")
    workflow.add_edge("generate", "validate")
    workflow.add_edge("validate", "persist")
    workflow.add_edge("persist", END)
    return workflow.compile()


_compiled_graph = None


def recommendation_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_recommendation_graph()
    return _compiled_graph


async def run_recommendation_graph(
    access_token: str,
    user_id: str,
    learner: dict[str, Any] | None,
) -> dict[str, Any]:
    from app.recommendations import RecommendationError

    graph_state = RecommendationGraphState()
    initial: PipelineState = {
        "access_token": access_token,
        "user_id": user_id,
        "learner": learner,
        "graph": graph_state,
        "failed": False,
    }
    final = await recommendation_graph().ainvoke(initial)
    if final.get("failed") or not final.get("result"):
        raise RecommendationError("A grounded recommendation could not be produced.")
    return final["result"]
