from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI

from app.catalog import CatalogError, list_products_by_ids
from app.config import settings
from app.interest import (
    InterestProfileError,
    build_interest_profile,
    get_interest_profile,
    profile_products,
    recent_profile_events,
)
from app.triggers import (
    is_recommendation_fresh,
    recommendation_expires_at,
)
from app.agent_graph import RecommendationGraphState, log_graph_state
from app.observability import event_logger, log_event
from app.vector_sync import VectorSyncError, semantic_product_matches

RECOMMENDATION_TIMEOUT = 30.0
logger = event_logger("skillorbit.recommendations")

LEVEL_ORDER = {"Beginner": 0, "Intermediate": 1, "Advanced": 2}


class RecommendationError(RuntimeError):
    """Raised when a grounded recommendation cannot be produced."""


def build_retrieval_query(
    profile: dict[str, Any] | None,
    learner: dict[str, Any] | None,
) -> str:
    profile = profile or {}
    learner = learner or {}
    weekly = learner.get("weekly_minutes")
    weekly_line = f"Weekly learning goal: {weekly} minutes" if weekly else ""
    parts = [
        f"Career goal: {learner.get('career_goal', '')}",
        f"Current level: {learner.get('current_level', '')}",
        weekly_line,
        "Interested categories: " + ", ".join(profile.get("interest_snapshot") or []),
        "Skills: " + ", ".join((profile.get("skill_weights") or {}).keys()),
        "Recent searches: " + ", ".join(profile.get("search_terms") or []),
    ]
    return "\n".join(part for part in parts if part.split(": ", 1)[-1].strip())


async def _mesh_narrative(
    query: str,
    profile: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not settings.mesh_configured:
        raise RecommendationError("Mesh API is not configured.")
    candidate_context = [
        {
            "id": product["id"],
            "title": product.get("title"),
            "category": product.get("category"),
            "difficulty": product.get("difficulty"),
            "skills": product.get("skills") or [],
            "summary": product.get("short_summary") or product.get("description", "")[:240],
        }
        for product in candidates
    ]
    system = (
        "You are SkillOrbit's grounded learning navigator. "
        "Return JSON only with keys summary, next_step, and item_reasons. "
        "item_reasons must be an array of objects with product_id and reason. "
        "Use only the supplied product IDs. Never invent products, IDs, skills, or user facts. "
        "Keep summary under 45 words, next_step under 30 words, and each reason under 25 words."
    )
    user = json.dumps({
        "learner_signals": {
            "summary": (profile or {}).get("signal_summary", "New learner with limited signals"),
            "interests": (profile or {}).get("interest_snapshot") or [],
            "skills": list(((profile or {}).get("skill_weights") or {}).keys()),
        },
        "retrieval_query": query,
        "grounded_candidates": candidate_context,
    }, ensure_ascii=True)
    client = AsyncOpenAI(
        base_url=settings.mesh_api_base_url,
        api_key=settings.mesh_api_key,
        timeout=RECOMMENDATION_TIMEOUT,
    )
    try:
        response = await client.chat.completions.create(
            model=settings.mesh_chat_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content if response.choices else ""
        parsed = json.loads(content or "{}")
    except Exception as error:
        raise RecommendationError("The recommendation explanation could not be generated.") from error
    finally:
        await client.close()
    if not isinstance(parsed, dict):
        raise RecommendationError("The recommendation explanation was invalid.")
    return parsed


def _safe_narrative(
    narrative: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[str, str, list[dict[str, str]]]:
    valid_ids = {product["id"] for product in candidates}
    reasons: dict[str, str] = {}
    for item in narrative.get("item_reasons") or []:
        if isinstance(item, dict) and item.get("product_id") in valid_ids:
            reasons[item["product_id"]] = str(item.get("reason") or "Matches your current learning direction.")[:240]
    safe_items = [
        {"product_id": product["id"], "reason": reasons.get(
            product["id"], "A grounded match for your current learning signals."
        )}
        for product in candidates
    ]
    summary = str(narrative.get("summary") or "A focused set of resources matched to your learning signals.")[:500]
    next_step = str(narrative.get("next_step") or "Start with the first resource and reflect after completing it.")[:300]
    return summary, next_step, safe_items


def _evaluate_candidates(
    candidates: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    learner: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    scores = {match["product_id"]: match["score"] for match in matches}
    goal = (learner or {}).get("career_goal") or ""
    level = (learner or {}).get("current_level") or ""
    level_idx = LEVEL_ORDER.get(level, 1)

    def rank_score(product: dict[str, Any]) -> float:
        base = float(scores.get(product["id"], 0))
        goals = product.get("career_goals") or []
        if goal and goal in goals:
            base += 0.06
        product_level = LEVEL_ORDER.get(product.get("difficulty") or "", 1)
        if product_level == level_idx:
            base += 0.04
        elif abs(product_level - level_idx) == 1:
            base += 0.02
        return base

    ranked = sorted(candidates, key=rank_score, reverse=True)
    selected: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    for product in ranked:
        category = product.get("category") or "General"
        if category_counts.get(category, 0) >= 2 and len(selected) >= 2:
            continue
        selected.append(product)
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= limit:
            break
    if not selected:
        return ranked[:limit]
    return selected


async def _stage_analyze(
    access_token: str,
    user_id: str,
    learner: dict[str, Any] | None,
    state: RecommendationGraphState,
) -> tuple[dict[str, Any], str]:
    analyze_started = state.start_stage("analyze")
    try:
        profile = await get_interest_profile(access_token, user_id)
        if not profile:
            events = await recent_profile_events(access_token, user_id, limit=100)
            resource_ids = list(dict.fromkeys(
                event["resource_id"] for event in events if event.get("resource_id")
            ))
            products = await profile_products(access_token, resource_ids)
            profile = build_interest_profile(events, products, learner)
        query = build_retrieval_query(profile, learner)
        if not query:
            query = "A practical beginner-friendly learning path for building useful software products"
        state.finish_stage(
            "analyze",
            analyze_started,
            event_count=profile.get("event_count", 0),
            meaningful_event_count=profile.get("meaningful_event_count", 0),
        )
        return profile, query
    except Exception as error:
        state.fail_stage("analyze", analyze_started, "profile_unavailable")
        if isinstance(error, RecommendationError):
            raise
        raise RecommendationError("Learner signals could not be analyzed.") from error


async def _stage_retrieve(
    query: str,
    state: RecommendationGraphState,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    retrieve_started = state.start_stage("retrieve")
    try:
        matches = await semantic_product_matches(query, limit=8)
        ids = [match["product_id"] for match in matches]
        candidates = await list_products_by_ids(ids)
    except (VectorSyncError, CatalogError) as error:
        state.fail_stage("retrieve", retrieve_started, "catalog_unavailable")
        raise RecommendationError("Grounded catalog retrieval is temporarily unavailable.") from error
    state.retrieval = {
        "candidate_count": len(matches),
        "catalog_match_count": len(candidates),
        "top_score": matches[0]["score"] if matches else 0,
        "mean_score": round(sum(match["score"] for match in matches) / len(matches), 5) if matches else 0,
    }
    ordered = {product["id"]: product for product in candidates}
    candidates = [ordered[match["product_id"]] for match in matches if match["product_id"] in ordered]
    if not candidates:
        state.fail_stage("retrieve", retrieve_started, "no_grounded_matches")
        raise RecommendationError("No grounded learning resources matched yet.")
    state.finish_stage("retrieve", retrieve_started, match_count=len(candidates), **state.retrieval)
    return matches, candidates


def _stage_evaluate(
    candidates: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    learner: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    state: RecommendationGraphState,
) -> list[dict[str, Any]]:
    evaluate_started = state.start_stage("evaluate")
    relevant_candidates = _evaluate_candidates(candidates, matches, learner, profile, limit=5)
    state.finish_stage(
        "evaluate",
        evaluate_started,
        selected_count=len(relevant_candidates),
        score_threshold=state.retrieval.get("mean_score", 0),
    )
    return relevant_candidates


async def _insert_recommendation(
    access_token: str,
    user_id: str,
    profile: dict[str, Any],
    query: str,
    summary: str,
    next_step: str,
    items: list[dict[str, str]],
    model: str,
    state: RecommendationGraphState,
) -> dict[str, Any]:
    import httpx

    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    recommendation = {
        "user_id": user_id,
        "summary": summary,
        "next_step": next_step,
        "interest_snapshot": profile.get("interest_snapshot") or [],
        "retrieval_query": query[:1200],
        "model": model,
        "trigger_event_count": profile.get("meaningful_event_count", 0),
        "status": "active",
        "trace_id": state.trace_id,
        "retrieval_metadata": state.retrieval,
        "expires_at": recommendation_expires_at(),
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{settings.supabase_url}/rest/v1/recommendations",
                headers=headers,
                json=recommendation,
            )
            if response.is_error:
                raise RecommendationError("Recommendation history could not be saved.")
            rows = response.json()
            saved = rows[0] if isinstance(rows, list) and rows else None
            if not saved:
                raise RecommendationError("Recommendation history could not be saved.")
            item_response = await client.post(
                f"{settings.supabase_url}/rest/v1/recommendation_items",
                headers=headers,
                json=[
                    {
                        "recommendation_id": saved["id"],
                        "product_id": item["product_id"],
                        "rank": index,
                        "reason": item["reason"],
                        "retrieval_score": item.get("score"),
                    }
                    for index, item in enumerate(items, start=1)
                ],
            )
            if item_response.is_error:
                raise RecommendationError("Recommendation items could not be saved.")
            return {**saved, "items": items}
    except RecommendationError:
        raise
    except httpx.HTTPError as error:
        raise RecommendationError("Recommendation history is temporarily unavailable.") from error


async def recommendation_api_payload(
    recommendation: dict[str, Any],
    *,
    cached: bool = False,
) -> dict[str, Any]:
    """Serialize a stored recommendation for dashboard/API clients."""
    items = list(recommendation.get("items") or [])
    if items and not items[0].get("title"):
        product_ids = [str(item["product_id"]) for item in items if item.get("product_id")]
        products = await list_products_by_ids(product_ids)
        product_map = {product["id"]: product for product in products}
        enriched: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            product = product_map.get(item.get("product_id"), {})
            enriched.append({
                **item,
                "rank": item.get("rank") or index,
                "title": product.get("title", "Learning resource"),
                "category": product.get("category", ""),
                "difficulty": product.get("difficulty", ""),
            })
        items = enriched
    return {
        "id": recommendation["id"],
        "summary": recommendation["summary"],
        "next_step": recommendation["next_step"],
        "items": items,
        "trace_id": recommendation.get("trace_id"),
        "retrieval_metadata": recommendation.get("retrieval_metadata") or {},
        "trigger_event_count": recommendation.get("trigger_event_count", 0),
        "interest_snapshot": recommendation.get("interest_snapshot") or [],
        "created_at": recommendation.get("created_at"),
        "expires_at": recommendation.get("expires_at"),
        "model": recommendation.get("model", "mesh"),
        "cached": cached,
    }


async def generate_recommendation(
    access_token: str,
    user_id: str,
    learner: dict[str, Any] | None,
) -> dict[str, Any]:
    from app.langgraph_agent import run_recommendation_graph

    log_event(logger, logging.INFO, "recommendation_pipeline_started", orchestrator="langgraph")
    return await run_recommendation_graph(access_token, user_id, learner)


async def latest_recommendation(access_token: str, user_id: str) -> dict[str, Any] | None:
    import httpx

    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/recommendations",
                headers=headers,
                params={
                    "select": "*",
                    "user_id": f"eq.{user_id}",
                    "status": "eq.active",
                    "order": "created_at.desc",
                    "limit": "1",
                },
            )
            if response.is_error:
                raise RecommendationError("Recommendations are temporarily unavailable.")
            rows = response.json()
            if not rows:
                return None
            recommendation = rows[0]
            if not is_recommendation_fresh(recommendation):
                await update_recommendation_status(
                    access_token, user_id, recommendation["id"], "expired"
                )
                return None
            items_response = await client.get(
                f"{settings.supabase_url}/rest/v1/recommendation_items",
                headers=headers,
                params={
                    "select": "product_id,rank,reason,retrieval_score",
                    "recommendation_id": f"eq.{recommendation['id']}",
                    "order": "rank.asc",
                },
            )
            if items_response.is_error:
                raise RecommendationError("Recommendation items are temporarily unavailable.")
            items = items_response.json()
            product_ids = [item.get("product_id") for item in items if item.get("product_id")]
            try:
                products = await list_products_by_ids(product_ids)
            except CatalogError as error:
                raise RecommendationError("Recommended resources are temporarily unavailable.") from error
            product_map = {product["id"]: product for product in products}
            recommendation["items"] = [
                {
                    **item,
                    "title": product_map.get(item.get("product_id"), {}).get("title", "Learning resource"),
                    "category": product_map.get(item.get("product_id"), {}).get("category", ""),
                    "difficulty": product_map.get(item.get("product_id"), {}).get("difficulty", ""),
                }
                for item in items
                if item.get("product_id") in product_map
            ]
            recommendation["graph_stages"] = _trace_stages_from_metadata(recommendation)
            return recommendation
    except RecommendationError:
        raise
    except httpx.HTTPError as error:
        raise RecommendationError("Recommendations are temporarily unavailable.") from error


def _trace_stages_from_metadata(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = recommendation.get("retrieval_metadata") or {}
    trace_id = recommendation.get("trace_id") or ""
    stages = [
        {"name": "analyze", "status": "completed", "meaningful_event_count": recommendation.get("trigger_event_count", 0)},
        {
            "name": "retrieve",
            "status": "completed",
            "catalog_match_count": metadata.get("catalog_match_count", 0),
            "top_score": metadata.get("top_score", 0),
        },
        {"name": "evaluate", "status": "completed", "selected_count": len(recommendation.get("items") or [])},
        {"name": "generate", "status": "completed", "model": recommendation.get("model", "")},
        {"name": "validate", "status": "completed"},
        {"name": "persist", "status": "completed", "trace_id": trace_id},
    ]
    return stages


async def recommendation_history(
    access_token: str,
    user_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    import httpx

    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/recommendations",
                headers=headers,
                params={
                    "select": "id,summary,next_step,status,model,trigger_event_count,created_at,trace_id,retrieval_metadata",
                    "user_id": f"eq.{user_id}",
                    "order": "created_at.desc",
                    "limit": str(max(1, min(limit, 10))),
                },
            )
    except httpx.HTTPError as error:
        raise RecommendationError("Recommendation history is temporarily unavailable.") from error
    if response.is_error:
        raise RecommendationError("Recommendation history is temporarily unavailable.")
    rows = response.json()
    return rows if isinstance(rows, list) else []


async def update_recommendation_status(
    access_token: str,
    user_id: str,
    recommendation_id: str,
    status: str,
) -> None:
    import httpx

    if status not in {"active", "dismissed", "expired"}:
        raise RecommendationError("That feedback is not supported.")
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.patch(
                f"{settings.supabase_url}/rest/v1/recommendations",
                headers=headers,
                params={
                    "id": f"eq.{recommendation_id}",
                    "user_id": f"eq.{user_id}",
                },
                json={"status": status},
            )
    except httpx.HTTPError as error:
        raise RecommendationError("Recommendation feedback could not be saved.") from error
    if response.is_error:
        raise RecommendationError("Recommendation feedback could not be saved.")
