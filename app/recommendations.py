from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI

from app.catalog import CatalogError, list_products, list_products_by_ids, list_products_for_goal
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
RETRIEVAL_POOL_SIZE = 20
RETRIEVAL_FINAL_SIZE = 5

MODERATION_MESSAGES = {
    "empty_or_short": "Not enough learning signals yet. Explore a few catalog resources first.",
    "toxic_content": "We could not build a safe learning path from those signals. Try focusing on technical topics.",
    "off_topic": "SkillOrbit only generates learning paths. Browse the catalog to build grounded study signals.",
}

TOXIC_QUERY_FRAGMENTS = (
    "kill yourself",
    "hate speech",
    "bomb making",
    "illegal drugs",
)

LEARNING_QUERY_HINTS = (
    "learn",
    "learning",
    "career",
    "skill",
    "course",
    "tutorial",
    "python",
    "backend",
    "engineer",
    "developer",
    "data",
    "software",
    "resource",
    "beginner",
    "intermediate",
    "advanced",
    "goal",
    "interest",
    "search",
    "catalog",
    "path",
)


class RecommendationError(RuntimeError):
    """Raised when a grounded recommendation cannot be produced."""


def moderate_retrieval_query(query: str) -> tuple[bool, str]:
    """Block empty, toxic, or clearly off-topic queries before Mesh generation."""
    cleaned = (query or "").strip()
    if len(cleaned) < 12:
        return False, "empty_or_short"
    lower = cleaned.lower()
    for fragment in TOXIC_QUERY_FRAGMENTS:
        if fragment in lower:
            return False, "toxic_content"
    if not any(hint in lower for hint in LEARNING_QUERY_HINTS):
        return False, "off_topic"
    return True, "allowed"


def moderation_error_message(reason: str) -> str:
    return MODERATION_MESSAGES.get(reason, MODERATION_MESSAGES["off_topic"])


def build_feedback_influence_metadata(profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = profile or {}
    return {
        "deprioritized_categories": dict(profile.get("feedback_penalties") or {}),
        "boosted_categories": dict(profile.get("feedback_boosts") or {}),
        "category_weights": dict(profile.get("category_weights") or {}),
    }


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
        category = product.get("category") or "General"
        penalties = (profile or {}).get("feedback_penalties") or {}
        boosts = (profile or {}).get("feedback_boosts") or {}
        penalty_count = int(penalties.get(category, 0))
        if penalty_count >= 3:
            base -= 0.18
        elif penalty_count > 0:
            base -= min(0.12, 0.04 * penalty_count)
        boost_count = int(boosts.get(category, 0))
        if boost_count > 0:
            base += min(0.1, 0.03 * boost_count)
        weights = (profile or {}).get("category_weights") or {}
        base += min(0.08, float(weights.get(category, 0)) * 0.02)
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


async def generic_baseline_path(
    learner: dict[str, Any] | None,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Non-personalized popularity-style path for counterfactual comparison."""
    goal = (learner or {}).get("career_goal") or "software engineering"
    level = (learner or {}).get("current_level") or "Beginner"
    query = (
        f"Popular general-audience learning resources for {goal} at {level} level. "
        "Broad catalog picks without personalized behavioral signals."
    )
    items: list[dict[str, Any]] = []
    source = "generic_retrieval"
    try:
        matches = await semantic_product_matches(query, limit=max(limit, 8))
        product_ids = [match["product_id"] for match in matches if match.get("product_id")]
        products = await list_products_by_ids(product_ids)
        product_map = {product["id"]: product for product in products}
        for index, match in enumerate(matches, start=1):
            product = product_map.get(match.get("product_id"))
            if not product:
                continue
            items.append({
                "rank": len(items) + 1,
                "product_id": product["id"],
                "title": product.get("title", "Learning resource"),
                "category": product.get("category", ""),
                "difficulty": product.get("difficulty", ""),
                "score": round(float(match.get("score", 0)), 4),
                "reason": "Generic catalog match without personal signals.",
            })
            if len(items) >= limit:
                break
    except (VectorSyncError, CatalogError):
        source = "catalog_fallback"
        try:
            fallback_products = await list_products_for_goal(goal)
        except CatalogError:
            fallback_products = []
        if not fallback_products:
            fallback_products = await list_products(career_goal=goal)
        for index, product in enumerate(fallback_products[:limit], start=1):
            items.append({
                "rank": index,
                "product_id": product["id"],
                "title": product.get("title", "Learning resource"),
                "category": product.get("category", ""),
                "difficulty": product.get("difficulty", ""),
                "score": None,
                "reason": "Popular fallback pick for your career goal.",
            })

    return {
        "query": query,
        "source": source,
        "items": items,
    }


def pipeline_timings_from_stages(stages: list[dict[str, Any]]) -> dict[str, int]:
    """Summarize per-stage durations for observability UI."""
    timings: dict[str, int] = {}
    total = 0
    for stage in stages:
        duration = stage.get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            name = str(stage.get("name") or "stage")
            timings[f"{name}_ms"] = int(duration)
            total += int(duration)
    timings["total_ms"] = total
    return timings


def annotate_retrieval_candidates(
    retrieval: dict[str, Any],
    matches: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    selected_products: list[dict[str, Any]],
) -> None:
    """Attach ranked Qdrant hits with selected vs rejected flags for judges."""
    product_map = {product["id"]: product for product in candidates}
    selected_ids = {product["id"] for product in selected_products}
    ranked: list[dict[str, Any]] = []
    for index, match in enumerate(matches[:8], start=1):
        product_id = match.get("product_id")
        if not product_id:
            continue
        product = product_map.get(product_id, {})
        ranked.append({
            "rank": index,
            "product_id": product_id,
            "title": product.get("title", "Learning resource"),
            "category": product.get("category", ""),
            "difficulty": product.get("difficulty", ""),
            "score": round(float(match.get("score", 0)), 4),
            "selected": product_id in selected_ids,
        })
    retrieval["candidates"] = ranked
    retrieval["selected_count"] = len(selected_ids)
    retrieval["rejected_count"] = max(0, len(ranked) - len(selected_ids))


def finalize_retrieval_metadata(state: RecommendationGraphState) -> dict[str, Any]:
    """Persist observability payload alongside the recommendation."""
    metadata = dict(state.retrieval or {})
    metadata["pipeline_timings"] = pipeline_timings_from_stages(state.stages)
    metadata["pipeline_stages"] = [
        {
            key: stage.get(key)
            for key in (
                "name",
                "status",
                "duration_ms",
                "started_at",
                "completed_at",
                "match_count",
                "selected_count",
                "valid_item_count",
                "model",
                "error_code",
            )
            if stage.get(key) is not None
        }
        for stage in state.stages
    ]
    metadata["causality_timeline"] = build_causality_timeline(
        metadata.get("trigger_events") or [],
        state.stages,
    )
    return metadata


SIGNAL_EVENT_TYPES = {
    "catalog_search",
    "bookmark_added",
    "resource_view",
    "resource_click",
    "resource_dwell",
    "filter_applied",
    "learning_goal_updated",
}

SIGNAL_EVENT_LABELS = {
    "catalog_search": "Searched catalog",
    "bookmark_added": "Bookmarked resource",
    "resource_view": "Viewed resource",
    "resource_click": "Opened resource",
    "resource_dwell": "Focused reading",
    "filter_applied": "Applied filter",
    "learning_goal_updated": "Updated learning goal",
}


def format_signal_events(
    events: list[dict[str, Any]],
    products: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize recent learner events for the causality timeline."""
    formatted: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event_type", "")
        if event_type not in SIGNAL_EVENT_TYPES:
            continue
        resource = products.get(event.get("resource_id") or "")
        detail = ""
        if event_type == "catalog_search":
            detail = str(event.get("search_query") or "").strip()
        elif resource:
            detail = str(resource.get("title") or "Learning resource")
        elif event_type == "filter_applied":
            metadata = event.get("metadata") or {}
            detail = str(metadata.get("category") or metadata.get("difficulty") or "catalog filters")
        elif event_type == "learning_goal_updated":
            metadata = event.get("metadata") or {}
            detail = str(metadata.get("career_goal") or "learning goal")
        if not detail:
            continue
        formatted.append({
            "kind": "signal",
            "event_type": event_type,
            "label": SIGNAL_EVENT_LABELS.get(event_type, event_type.replace("_", " ").title()),
            "detail": detail[:160],
            "occurred_at": event.get("occurred_at"),
        })
    return formatted[:8]


def build_causality_timeline(
    signal_events: list[dict[str, Any]],
    stages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Chronological chain: learner signals, then LangGraph stages."""
    timeline: list[dict[str, Any]] = []
    sorted_signals = sorted(
        [event for event in signal_events if event.get("occurred_at")],
        key=lambda item: item["occurred_at"],
    )
    timeline.extend(sorted_signals)
    for stage in stages:
        timeline.append({
            "kind": "stage",
            "name": stage.get("name"),
            "label": str(stage.get("name") or "stage").replace("_", " ").title(),
            "status": stage.get("status"),
            "duration_ms": stage.get("duration_ms"),
            "started_at": stage.get("started_at"),
            "completed_at": stage.get("completed_at"),
            **{
                key: stage[key]
                for key in ("match_count", "selected_count", "valid_item_count", "model")
                if stage.get(key) is not None
            },
        })
    return timeline


def fallback_change_explanation(
    profile: dict[str, Any] | None,
    signal_events: list[dict[str, Any]],
    previous: dict[str, Any],
    new_products: list[dict[str, Any]],
) -> str:
    """Deterministic explanation when Mesh is unavailable."""
    previous_titles = [item.get("title") for item in previous.get("items") or [] if item.get("title")]
    new_titles = [product.get("title") for product in new_products if product.get("title")]
    added = [title for title in new_titles if title not in previous_titles]
    removed = [title for title in previous_titles if title not in new_titles]
    signal_bits = [
        f"{event.get('label')}: {event.get('detail')}"
        for event in signal_events[:3]
        if event.get("detail")
    ]
    parts = []
    if signal_bits:
        parts.append("Your recent activity (" + "; ".join(signal_bits) + ") shifted retrieval.")
    elif profile and profile.get("signal_summary"):
        parts.append(f"Signals updated ({profile['signal_summary']}).")
    if added:
        parts.append("New resources surfaced: " + ", ".join(added[:3]) + ".")
    if removed:
        parts.append("Earlier picks like " + ", ".join(removed[:2]) + " were deprioritized.")
    if not parts:
        parts.append("Your learning signals changed enough to refresh the grounded path.")
    return " ".join(parts)[:900]


async def _mesh_change_explanation(
    profile: dict[str, Any] | None,
    signal_events: list[dict[str, Any]],
    previous: dict[str, Any],
    new_summary: str,
    new_next_step: str,
    new_products: list[dict[str, Any]],
) -> str:
    if not settings.mesh_configured:
        return fallback_change_explanation(profile, signal_events, previous, new_products)
    previous_items = [
        {
            "title": item.get("title"),
            "reason": item.get("reason"),
        }
        for item in (previous.get("items") or [])
        if item.get("title")
    ]
    new_items = [
        {
            "title": product.get("title"),
            "category": product.get("category"),
            "difficulty": product.get("difficulty"),
        }
        for product in new_products
        if product.get("title")
    ]
    system = (
        "You explain why a learner's grounded study path changed. "
        "Write 2-3 sentences in plain English. "
        "Ground every claim in the supplied behavioral signals and catalog titles only. "
        "Never invent products, searches, bookmarks, or user facts. "
        "Return JSON with a single key: explanation."
    )
    user = json.dumps({
        "behavioral_signals": [
            {
                "label": event.get("label"),
                "detail": event.get("detail"),
                "occurred_at": event.get("occurred_at"),
            }
            for event in signal_events
        ],
        "signal_summary": (profile or {}).get("signal_summary"),
        "previous_path": {
            "summary": previous.get("summary"),
            "next_step": previous.get("next_step"),
            "items": previous_items,
        },
        "new_path": {
            "summary": new_summary,
            "next_step": new_next_step,
            "items": new_items,
        },
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
        explanation = str(parsed.get("explanation") or "").strip()
    except Exception as error:
        log_event(logger, logging.WARNING, "change_explanation_mesh_failed", error=str(error))
        return fallback_change_explanation(profile, signal_events, previous, new_products)
    finally:
        await client.close()
    if not explanation:
        return fallback_change_explanation(profile, signal_events, previous, new_products)
    return explanation[:900]


async def _expire_active_recommendations(access_token: str, user_id: str) -> None:
    import httpx

    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.patch(
                f"{settings.supabase_url}/rest/v1/recommendations",
                headers=headers,
                params={
                    "user_id": f"eq.{user_id}",
                    "status": "eq.active",
                },
                json={"status": "expired"},
            )
    except httpx.HTTPError:
        return


async def _patch_recommendation_fields(
    access_token: str,
    recommendation_id: str,
    payload: dict[str, Any],
) -> None:
    import httpx

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
                params={"id": f"eq.{recommendation_id}"},
                json=payload,
            )
            if response.is_error:
                raise RecommendationError("Recommendation metadata could not be updated.")
    except httpx.HTTPError as error:
        raise RecommendationError("Recommendation metadata is temporarily unavailable.") from error


async def _stage_analyze(
    access_token: str,
    user_id: str,
    learner: dict[str, Any] | None,
    state: RecommendationGraphState,
) -> tuple[dict[str, Any], str]:
    analyze_started = state.start_stage("analyze")
    try:
        events = await recent_profile_events(access_token, user_id, limit=30)
        profile = await get_interest_profile(access_token, user_id)
        if not profile:
            resource_ids = list(dict.fromkeys(
                event["resource_id"] for event in events if event.get("resource_id")
            ))
            products = await profile_products(access_token, resource_ids)
            profile = build_interest_profile(events, products, learner)
        else:
            resource_ids = list(dict.fromkeys(
                event["resource_id"] for event in events if event.get("resource_id")
            ))
            products = await profile_products(access_token, resource_ids)
        if state.retrieval is None:
            state.retrieval = {}
        state.retrieval["trigger_events"] = format_signal_events(events, products)
        state.retrieval["category_weights_snapshot"] = profile.get("category_weights") or {}
        from app.interest import summarize_feedback_signals

        penalties, boosts = summarize_feedback_signals(events)
        profile["feedback_penalties"] = penalties
        profile["feedback_boosts"] = boosts
        state.retrieval["feedback_influence"] = build_feedback_influence_metadata(profile)
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
        matches = await semantic_product_matches(query, limit=RETRIEVAL_POOL_SIZE)
        ids = [match["product_id"] for match in matches]
        candidates = await list_products_by_ids(ids)
    except (VectorSyncError, CatalogError) as error:
        state.fail_stage("retrieve", retrieve_started, "catalog_unavailable")
        raise RecommendationError("Grounded catalog retrieval is temporarily unavailable.") from error
    state.retrieval = {
        **(state.retrieval or {}),
        "candidate_count": len(matches),
        "catalog_match_count": len(candidates),
        "rerank_count": len(candidates),
        "rerank_pool_size": RETRIEVAL_POOL_SIZE,
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
    relevant_candidates = _evaluate_candidates(
        candidates,
        matches,
        learner,
        profile,
        limit=RETRIEVAL_FINAL_SIZE,
    )
    annotate_retrieval_candidates(state.retrieval, matches, candidates, relevant_candidates)
    state.retrieval["rerank_selected"] = len(relevant_candidates)
    state.finish_stage(
        "evaluate",
        evaluate_started,
        selected_count=len(relevant_candidates),
        rerank_count=state.retrieval.get("rerank_count", 0),
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
    *,
    change_explanation: str | None = None,
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
        "retrieval_metadata": finalize_retrieval_metadata(state),
        "expires_at": recommendation_expires_at(),
    }
    if change_explanation:
        recommendation["change_explanation"] = change_explanation
    try:
        await _expire_active_recommendations(access_token, user_id)
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
    metadata = recommendation.get("retrieval_metadata") or {}
    return {
        "id": recommendation["id"],
        "summary": recommendation["summary"],
        "next_step": recommendation["next_step"],
        "items": items,
        "trace_id": recommendation.get("trace_id"),
        "retrieval_metadata": metadata,
        "change_explanation": recommendation.get("change_explanation"),
        "causality_timeline": metadata.get("causality_timeline") or [],
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
    stored = metadata.get("pipeline_stages")
    if isinstance(stored, list) and stored:
        return stored
    trace_id = recommendation.get("trace_id") or ""
    stages = [
        {"name": "analyze", "status": "completed", "meaningful_event_count": recommendation.get("trigger_event_count", 0)},
        {
            "name": "retrieve",
            "status": "completed",
            "catalog_match_count": metadata.get("catalog_match_count", 0),
            "top_score": metadata.get("top_score", 0),
        },
        {"name": "evaluate", "status": "completed", "selected_count": metadata.get("selected_count", len(recommendation.get("items") or []))},
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


async def recommendation_item_categories(
    access_token: str,
    recommendation_id: str,
) -> list[str]:
    import httpx

    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/recommendation_items",
                headers=headers,
                params={
                    "select": "product_id",
                    "recommendation_id": f"eq.{recommendation_id}",
                },
            )
    except httpx.HTTPError as error:
        raise RecommendationError("Recommendation items are temporarily unavailable.") from error
    if response.is_error:
        raise RecommendationError("Recommendation items are temporarily unavailable.")
    rows = response.json()
    product_ids = [row["product_id"] for row in rows if row.get("product_id")]
    if not product_ids:
        return []
    products = await list_products_by_ids(product_ids)
    return list(dict.fromkeys(
        product.get("category")
        for product in products
        if product.get("category")
    ))


def sanitize_recommendation_for_share(recommendation: dict[str, Any]) -> dict[str, Any]:
    """Public read-only payload — no user identifiers or personal interest data."""
    metadata = recommendation.get("retrieval_metadata") or {}
    public_metadata = {
        key: metadata[key]
        for key in (
            "catalog_match_count",
            "top_score",
            "mean_score",
            "selected_count",
            "pipeline_timings",
        )
        if key in metadata
    }
    items: list[dict[str, Any]] = []
    for index, item in enumerate(recommendation.get("items") or [], start=1):
        items.append({
            "rank": item.get("rank") or index,
            "title": item.get("title", "Learning resource"),
            "category": item.get("category", ""),
            "difficulty": item.get("difficulty", ""),
            "reason": item.get("reason", ""),
        })
    return {
        "id": recommendation["id"],
        "summary": recommendation["summary"],
        "next_step": recommendation["next_step"],
        "items": items,
        "trace_id": recommendation.get("trace_id"),
        "created_at": recommendation.get("created_at"),
        "model": recommendation.get("model", "mesh"),
        "trigger_event_count": recommendation.get("trigger_event_count", 0),
        "retrieval_metadata": public_metadata,
    }


async def get_recommendation_for_share(recommendation_id: str) -> dict[str, Any] | None:
    """Fetch a recommendation by ID using the service role (bypasses per-user RLS)."""
    import httpx

    service_key = settings.supabase_service_role_key
    if not service_key:
        raise RecommendationError("Share links are not configured on this deployment.")
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/recommendations",
                headers=headers,
                params={
                    "select": "*",
                    "id": f"eq.{recommendation_id}",
                    "limit": "1",
                },
            )
            if response.is_error:
                raise RecommendationError("This learning path could not be loaded.")
            rows = response.json()
            if not rows:
                return None
            recommendation = rows[0]
            items_response = await client.get(
                f"{settings.supabase_url}/rest/v1/recommendation_items",
                headers=headers,
                params={
                    "select": "*",
                    "recommendation_id": f"eq.{recommendation_id}",
                    "order": "rank.asc",
                },
            )
            if items_response.is_error:
                raise RecommendationError("This learning path could not be loaded.")
            recommendation["items"] = items_response.json()
    except RecommendationError:
        raise
    except httpx.HTTPError as error:
        raise RecommendationError("This learning path could not be loaded.") from error

    enriched = await recommendation_api_payload(recommendation)
    return sanitize_recommendation_for_share(enriched)


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
