from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.interest import (
    build_interest_profile,
    get_interest_profile,
    profile_products,
    recent_profile_events,
)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def compute_path_health(
    recommendation: dict[str, Any] | None,
    interest_profile: dict[str, Any] | None,
    progress_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    """Score path vitality from signal freshness, retrieval quality, and progress."""
    if not recommendation:
        return {
            "score": 0,
            "label": "No path",
            "factors": [],
        }

    metadata = recommendation.get("retrieval_metadata") or {}
    factors: list[dict[str, Any]] = []

    freshness = 40
    freshness_detail = "Signals are current."
    if interest_profile and interest_profile.get("refresh_recommended"):
        freshness = 14
        freshness_detail = "New activity suggests a refresh."
    last_event_at = _parse_timestamp((interest_profile or {}).get("last_event_at"))
    if last_event_at:
        age_days = (datetime.now(timezone.utc) - last_event_at).days
        if age_days >= 7:
            freshness = min(freshness, 10)
            freshness_detail = f"Last meaningful signal {age_days} days ago."
        elif age_days >= 3:
            freshness = min(freshness, 24)
            freshness_detail = f"Last meaningful signal {age_days} days ago."
    factors.append({
        "name": "Signal freshness",
        "score": freshness,
        "max": 40,
        "detail": freshness_detail,
    })

    top_score = float(metadata.get("top_score") or 0)
    mean_score = float(metadata.get("mean_score") or 0)
    match_quality = 10
    if top_score:
        match_quality = min(35, int(round(top_score * 28 + mean_score * 7)))
    match_detail = (
        f"Top retrieval score {round(top_score, 3)} · mean {round(mean_score, 3)}"
        if top_score
        else "Retrieval scores unavailable."
    )
    factors.append({
        "name": "Match quality",
        "score": match_quality,
        "max": 35,
        "detail": match_detail,
    })

    path_percent = int((progress_stats or {}).get("path_percent") or 0)
    progress_score = min(25, int(path_percent * 0.2) + (6 if path_percent > 0 else 0))
    factors.append({
        "name": "Progress momentum",
        "score": progress_score,
        "max": 25,
        "detail": f"{path_percent}% of your current path completed.",
    })

    total = sum(factor["score"] for factor in factors)
    if total >= 80:
        label = "Excellent"
    elif total >= 60:
        label = "Healthy"
    elif total >= 40:
        label = "Fair"
    else:
        label = "Needs refresh"

    return {
        "score": total,
        "label": label,
        "factors": factors,
    }


async def compute_interest_drift(
    access_token: str,
    user_id: str,
    learner: dict[str, Any] | None,
    current_profile: dict[str, Any] | None,
    *,
    last_recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare current category weights against last path or events from 7+ days ago."""
    current = dict((current_profile or {}).get("category_weights") or {})
    previous: dict[str, float] = {}
    baseline_label = "7 days ago"

    if last_recommendation:
        snapshot = (last_recommendation.get("retrieval_metadata") or {}).get("category_weights_snapshot")
        if isinstance(snapshot, dict) and snapshot:
            previous = {str(key): float(value) for key, value in snapshot.items()}
            baseline_label = "Last path"

    if not previous:
        events = await recent_profile_events(access_token, user_id, limit=200)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        old_events = [
            event for event in events
            if (_parse_timestamp(event.get("occurred_at")) or datetime.min.replace(tzinfo=timezone.utc)) < cutoff
        ]
        if old_events:
            resource_ids = list(dict.fromkeys(
                event["resource_id"] for event in old_events if event.get("resource_id")
            ))
            products = await profile_products(access_token, resource_ids)
            old_profile = build_interest_profile(old_events, products, learner)
            previous = dict(old_profile.get("category_weights") or {})

    categories = sorted(set(current) | set(previous), key=lambda name: (-current.get(name, 0), name))
    rows: list[dict[str, Any]] = []
    max_weight = 1.0
    for category in categories:
        cur = round(float(current.get(category, 0)), 2)
        prev = round(float(previous.get(category, 0)), 2)
        max_weight = max(max_weight, cur, prev)
        rows.append({
            "category": category,
            "current": cur,
            "previous": prev,
            "delta": round(cur - prev, 2),
        })
    rows.sort(key=lambda row: row["current"], reverse=True)

    return {
        "baseline_label": baseline_label,
        "categories": rows[:6],
        "max_weight": max_weight,
        "has_baseline": bool(previous),
    }


async def build_path_intelligence(
    access_token: str,
    user_id: str,
    learner: dict[str, Any] | None,
    recommendation: dict[str, Any] | None,
    interest_profile: dict[str, Any] | None,
    progress_stats: dict[str, Any] | None,
    *,
    last_recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bundle counterfactual, health score, and drift for dashboard/API clients."""
    from app.recommendations import generic_baseline_path

    generic_baseline = await generic_baseline_path(learner)
    personalized_items = list((recommendation or {}).get("items") or [])
    overlap = {
        item.get("product_id")
        for item in personalized_items
        if item.get("product_id")
    } & {
        item.get("product_id")
        for item in generic_baseline.get("items") or []
        if item.get("product_id")
    }
    return {
        "generic_baseline": generic_baseline,
        "personalized_items": personalized_items,
        "overlap_count": len(overlap),
        "path_health": compute_path_health(recommendation, interest_profile, progress_stats),
        "interest_drift": await compute_interest_drift(
            access_token,
            user_id,
            learner,
            interest_profile,
            last_recommendation=last_recommendation,
        ),
    }


async def recommendation_api_with_intelligence(
    access_token: str,
    user_id: str,
    learner: dict[str, Any] | None,
    recommendation: dict[str, Any],
    *,
    cached: bool = False,
    interest_profile: dict[str, Any] | None = None,
    progress_stats: dict[str, Any] | None = None,
    last_recommendation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.progress import list_progress, progress_summary
    from app.recommendations import recommendation_api_payload

    payload = await recommendation_api_payload(recommendation, cached=cached)
    profile = interest_profile
    stats = progress_stats
    if profile is None:
        profile = await get_interest_profile(access_token, user_id)
    if stats is None:
        path_ids = [
            item.get("product_id")
            for item in recommendation.get("items") or []
            if item.get("product_id")
        ]
        progress_rows = await list_progress(access_token, user_id)
        stats = progress_summary(progress_rows, path_ids)
    payload["path_intelligence"] = await build_path_intelligence(
        access_token,
        user_id,
        learner,
        recommendation,
        profile,
        stats,
        last_recommendation=last_recommendation,
    )
    return payload
