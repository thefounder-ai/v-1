from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.catalog import list_products
from app.config import settings

TIMEOUT = httpx.Timeout(30.0, connect=5.0)

DEMO_STEPS = [
    {
        "title": "Semantic discovery",
        "href": "/explore?search=production+RAG",
        "detail": "Search the public catalog — Qdrant retrieval, not keyword-only.",
    },
    {
        "title": "Build signals",
        "href": "/explore",
        "detail": "Open two resources, dwell, bookmark one. Events batch silently.",
    },
    {
        "title": "Learner dashboard",
        "href": "/dashboard",
        "detail": "Interest radar, live activity feed, and path health intelligence.",
    },
    {
        "title": "Generate grounded path",
        "href": "/dashboard",
        "detail": "LangGraph pipeline: analyze → retrieve → evaluate → moderate → generate.",
        "action": "generate",
    },
    {
        "title": "Show observability",
        "href": "/trace",
        "detail": "Qdrant scores, stage timings, and Mesh trace ID for judges.",
    },
    {
        "title": "Refresh + diff",
        "href": "/dashboard",
        "detail": "Change behavior, refresh path, read Why it changed + causality timeline.",
        "action": "refresh",
    },
]


def _service_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for demo seeding.")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def find_user_id_by_email(email: str) -> str | None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/auth/v1/admin/users",
            headers=_service_headers(),
            params={"email": email},
        )
    if response.is_error:
        return None
    users = response.json().get("users") or []
    for user in users:
        if user.get("email") == email:
            return user.get("id")
    return None


async def ensure_demo_profile(user_id: str) -> None:
    payload = {
        "user_id": user_id,
        "role": "learner",
        "career_goal": "AI Engineer",
        "current_level": "Intermediate",
        "weekly_minutes": 300,
        "onboarding_complete": True,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await client.post(
            f"{settings.supabase_url}/rest/v1/profiles",
            headers={**_service_headers(), "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "user_id"},
            json=payload,
        )


async def seed_demo_events(user_id: str, product_ids: list[str]) -> int:
    now = datetime.now(timezone.utc)
    events: list[dict[str, Any]] = []
    searches = ["production RAG", "vector search", "agent workflows"]
    for index, query in enumerate(searches):
        events.append({
            "event_id": str(uuid4()),
            "user_id": user_id,
            "event_type": "catalog_search",
            "search_query": query,
            "metadata": {"source": "demo_seed"},
            "occurred_at": (now - timedelta(hours=6 - index)).isoformat(),
        })
    for index, product_id in enumerate(product_ids[:4]):
        occurred = now - timedelta(hours=4 - index)
        events.append({
            "event_id": str(uuid4()),
            "user_id": user_id,
            "event_type": "resource_view",
            "resource_id": product_id,
            "metadata": {"source": "demo_seed"},
            "occurred_at": occurred.isoformat(),
        })
        events.append({
            "event_id": str(uuid4()),
            "user_id": user_id,
            "event_type": "resource_dwell",
            "resource_id": product_id,
            "duration_seconds": 45 + index * 15,
            "metadata": {"source": "demo_seed"},
            "occurred_at": (occurred + timedelta(seconds=30)).isoformat(),
        })
    if product_ids:
        events.append({
            "event_id": str(uuid4()),
            "user_id": user_id,
            "event_type": "bookmark_added",
            "resource_id": product_ids[0],
            "metadata": {"source": "demo_seed"},
            "occurred_at": (now - timedelta(minutes=30)).isoformat(),
        })
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"{settings.supabase_url}/rest/v1/activity_events",
            headers={**_service_headers(), "Prefer": "resolution=ignore-duplicates"},
            params={"on_conflict": "event_id"},
            json=events,
        )
    if response.is_error:
        raise RuntimeError(f"Could not insert demo events: {response.text[:300]}")
    return len(events)


async def apply_demo_seed(*, email: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    target_email = (email or os.environ.get("DEMO_USER_EMAIL", "")).strip()
    target_user_id = user_id
    if not target_user_id:
        if not target_email:
            raise ValueError("Provide email or user_id for demo seeding.")
        target_user_id = await find_user_id_by_email(target_email)
        if not target_user_id:
            raise ValueError(f"No Supabase user found for {target_email}.")

    products = await list_products(career_goal="AI Engineer")
    rag_products = [
        product for product in products
        if "rag" in (product.get("title") or "").lower()
        or "rag" in (product.get("short_summary") or "").lower()
    ]
    product_ids = [product["id"] for product in (rag_products or products)[:6]]
    await ensure_demo_profile(target_user_id)
    event_count = await seed_demo_events(target_user_id, product_ids)
    return {
        "user_id": target_user_id,
        "email": target_email or None,
        "events_seeded": event_count,
        "product_count": len(product_ids),
    }
