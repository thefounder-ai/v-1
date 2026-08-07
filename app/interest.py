from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings

INTEREST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
STOP_WORDS = {
    "and", "the", "for", "with", "from", "want", "build", "learn", "learn",
    "into", "how", "what", "your", "this", "that", "about", "course",
}


class InterestProfileError(RuntimeError):
    """Raised when learner interest data cannot be read or stored."""


def _headers(access_token: str, prefer: str | None = None) -> dict[str, str]:
    if not settings.supabase_configured:
        raise InterestProfileError("Supabase is not configured.")
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def _request(
    access_token: str,
    method: str,
    path: str,
    *,
    params: dict[str, str] | list[tuple[str, str]] | None = None,
    json: Any = None,
    prefer: str | None = None,
) -> Any:
    try:
        async with httpx.AsyncClient(timeout=INTEREST_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{settings.supabase_url}{path}",
                headers=_headers(access_token, prefer),
                params=params,
                json=json,
            )
    except (httpx.HTTPError, InterestProfileError) as error:
        raise InterestProfileError("Interest profile service is temporarily unavailable.") from error
    if response.is_error:
        raise InterestProfileError("Interest profile could not be updated.")
    if not response.content:
        return []
    return response.json()


async def recent_profile_events(
    access_token: str,
    user_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = await _request(
        access_token,
        "GET",
        "/rest/v1/activity_events",
        params={
            "select": "event_id,event_type,resource_id,search_query,duration_seconds,metadata,occurred_at",
            "user_id": f"eq.{user_id}",
            "order": "occurred_at.desc",
            "limit": str(max(1, min(limit, 200))),
        },
    )
    return rows if isinstance(rows, list) else []


async def profile_products(
    access_token: str,
    product_ids: list[str],
) -> dict[str, dict[str, Any]]:
    if not product_ids:
        return {}
    rows = await _request(
        access_token,
        "GET",
        "/rest/v1/products",
        params=[
            ("select", "id,title,category,skills,career_goals,difficulty"),
            ("id", f"in.({','.join(product_ids)})"),
        ],
    )
    return {row["id"]: row for row in rows if row.get("id")}


def _tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        token for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9+#.-]{2,}", value.lower())
        if token not in STOP_WORDS
    ]


def _add(counter: defaultdict[str, float], values: list[str], weight: float) -> None:
    for value in values:
        clean = value.strip()
        if clean:
            counter[clean] += weight


def build_interest_profile(
    events: list[dict[str, Any]],
    products: dict[str, dict[str, Any]],
    learner_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    category_weights: defaultdict[str, float] = defaultdict(float)
    skill_weights: defaultdict[str, float] = defaultdict(float)
    search_weights: Counter[str] = Counter()
    meaningful = 0
    total_dwell = 0

    for event in events:
        event_type = event.get("event_type", "")
        resource = products.get(event.get("resource_id"))
        if event_type in {"resource_view", "resource_click", "resource_dwell"}:
            meaningful += 1
            base_weight = {"resource_view": 1.0, "resource_click": 1.5, "resource_dwell": 1.0}[event_type]
            duration = int(event.get("duration_seconds") or 0)
            total_dwell += duration
            dwell_bonus = min(4.0, duration / 30.0) if event_type == "resource_dwell" else 0
            weight = base_weight + dwell_bonus
            if resource:
                _add(category_weights, [resource.get("category", "")], weight)
                _add(skill_weights, resource.get("skills") or [], weight)
        elif event_type == "catalog_search":
            meaningful += 1
            search_weights.update(_tokens(event.get("search_query")))
        elif event_type == "filter_applied":
            meaningful += 1
            metadata = event.get("metadata") or {}
            _add(category_weights, [str(metadata.get("category", ""))], 0.75)
        elif event_type == "bookmark_added":
            meaningful += 1
            if resource:
                _add(category_weights, [resource.get("category", "")], 1.25)
                _add(skill_weights, resource.get("skills") or [], 1.25)
        elif event_type == "learning_goal_updated":
            meaningful += 1
            metadata = event.get("metadata") or {}
            _add(category_weights, [str(metadata.get("career_goal", ""))], 1.0)

    top_categories = sorted(category_weights.items(), key=lambda item: (-item[1], item[0]))[:5]
    top_skills = sorted(skill_weights.items(), key=lambda item: (-item[1], item[0]))[:8]
    top_searches = [token for token, _ in search_weights.most_common(8)]
    snapshot = [name for name, _ in top_categories[:3]] + [name for name, _ in top_skills[:4]]
    snapshot = list(dict.fromkeys(snapshot))[:7]

    repeated_search = any(count >= 2 for count in search_weights.values())
    refresh_recommended = (
        meaningful >= 3
        or repeated_search
        or total_dwell >= 120
    )
    goal = (learner_profile or {}).get("career_goal")
    level = (learner_profile or {}).get("current_level")
    signals = []
    if top_categories:
        signals.append("interest in " + ", ".join(name for name, _ in top_categories[:2]))
    if top_searches:
        signals.append("searches around " + ", ".join(top_searches[:3]))
    if total_dwell:
        signals.append(f"{total_dwell // 60}m of focused reading")
    if not signals:
        signals.append("not enough activity yet")
    if goal:
        signals.append(f"goal: {goal}")

    return {
        "interest_snapshot": snapshot,
        "category_weights": {key: round(value, 2) for key, value in top_categories},
        "skill_weights": {key: round(value, 2) for key, value in top_skills},
        "search_terms": top_searches,
        "signal_summary": " · ".join(signals),
        "event_count": len(events),
        "meaningful_event_count": meaningful,
        "refresh_recommended": refresh_recommended,
        "last_event_at": events[0].get("occurred_at") if events else None,
        "profile_version": 1,
        "user_id": (learner_profile or {}).get("user_id"),
        "learner_goal": goal,
        "learner_level": level,
    }


async def refresh_interest_profile(
    access_token: str,
    user_id: str,
    learner_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = await recent_profile_events(access_token, user_id)
    product_ids = list(dict.fromkeys(
        event["resource_id"] for event in events if event.get("resource_id")
    ))
    products = await profile_products(access_token, product_ids)
    profile = build_interest_profile(events, products, learner_profile)
    profile["user_id"] = user_id
    existing = await get_interest_profile(access_token, user_id)
    profile["profile_version"] = int(existing.get("profile_version", 0)) + 1 if existing else 1
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    profile.pop("learner_goal", None)
    profile.pop("learner_level", None)
    await _request(
        access_token,
        "POST",
        "/rest/v1/user_interest_profiles",
        params={"on_conflict": "user_id"},
        json=profile,
        prefer="resolution=merge-duplicates,return=representation",
    )
    return profile


async def get_interest_profile(
    access_token: str,
    user_id: str,
) -> dict[str, Any] | None:
    rows = await _request(
        access_token,
        "GET",
        "/rest/v1/user_interest_profiles",
        params={"select": "*", "user_id": f"eq.{user_id}", "limit": "1"},
    )
    return rows[0] if isinstance(rows, list) and rows else None