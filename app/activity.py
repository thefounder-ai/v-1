from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from app.config import settings

ACTIVITY_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MEANINGFUL_EVENT_TYPES = {
    "resource_view",
    "resource_dwell",
    "bookmark_added",
    "catalog_search",
    "recommendation_opened",
    "recommendation_feedback",
}
EventType = Literal[
    "page_view",
    "catalog_search",
    "filter_applied",
    "resource_view",
    "resource_click",
    "resource_dwell",
    "bookmark_added",
    "recommendation_opened",
    "recommendation_feedback",
    "learning_goal_updated",
]


class ActivityEventInput(BaseModel):
    event_id: UUID
    event_type: EventType
    resource_id: UUID | None = None
    search_query: str | None = Field(default=None, max_length=200)
    duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class ActivityBatch(BaseModel):
    events: list[ActivityEventInput] = Field(min_length=1, max_length=50)


class ActivityError(RuntimeError):
    """Raised when activity storage cannot be reached."""


def _headers(access_token: str) -> dict[str, str]:
    if not settings.supabase_configured:
        raise ActivityError("Supabase is not configured.")
    return {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }


def _bounded_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if len(metadata) > 12:
        raise ActivityError("Event metadata has too many fields.")
    bounded = dict(list(metadata.items())[:12])
    try:
        serialized = json.dumps(bounded, ensure_ascii=True, default=str)
    except (TypeError, ValueError) as error:
        raise ActivityError("Event metadata is not valid JSON.") from error
    if len(serialized) > 4000:
        raise ActivityError("Event metadata is too large.")
    return bounded


def _event_payload(event: ActivityEventInput, user_id: str) -> dict[str, Any]:
    occurred_at = event.occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return {
        "event_id": str(event.event_id),
        "user_id": user_id,
        "event_type": event.event_type,
        "resource_id": str(event.resource_id) if event.resource_id else None,
        "search_query": event.search_query.strip() if event.search_query else None,
        "duration_seconds": event.duration_seconds,
        "metadata": _bounded_metadata(event.metadata),
        "occurred_at": occurred_at.isoformat(),
    }


async def store_events(
    access_token: str,
    user_id: str,
    batch: ActivityBatch,
) -> int:
    payload = [_event_payload(event, user_id) for event in batch.events]
    try:
        async with httpx.AsyncClient(timeout=ACTIVITY_TIMEOUT) as client:
            response = await client.post(
                f"{settings.supabase_url}/rest/v1/activity_events",
                headers=_headers(access_token),
                params={"on_conflict": "event_id"},
                json=payload,
            )
    except (httpx.HTTPError, ActivityError) as error:
        raise ActivityError("Activity could not be saved right now.") from error
    if response.is_error:
        raise ActivityError("Activity could not be saved right now.")
    return len(payload)


async def recent_events(
    access_token: str,
    user_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=ACTIVITY_TIMEOUT) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/activity_events",
                headers={
                    "apikey": settings.supabase_anon_key,
                    "Authorization": f"Bearer {access_token}",
                },
                params={
                    "select": "event_id,event_type,resource_id,search_query,duration_seconds,occurred_at",
                    "user_id": f"eq.{user_id}",
                    "order": "occurred_at.desc",
                    "limit": str(max(1, min(limit, 30))),
                },
            )
    except (httpx.HTTPError, ActivityError) as error:
        raise ActivityError("Recent activity is temporarily unavailable.") from error
    if response.is_error:
        raise ActivityError("Recent activity is temporarily unavailable.")
    rows = response.json()
    return rows if isinstance(rows, list) else []


async def events_since(
    access_token: str,
    user_id: str,
    since: datetime,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=ACTIVITY_TIMEOUT) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/activity_events",
                headers={
                    "apikey": settings.supabase_anon_key,
                    "Authorization": f"Bearer {access_token}",
                },
                params={
                    "select": "event_id,event_type,resource_id,search_query,duration_seconds,metadata,occurred_at",
                    "user_id": f"eq.{user_id}",
                    "occurred_at": f"gte.{since.isoformat()}",
                    "order": "occurred_at.asc",
                    "limit": str(max(1, min(limit, 30))),
                },
            )
    except (httpx.HTTPError, ActivityError) as error:
        raise ActivityError("Recent activity is temporarily unavailable.") from error
    if response.is_error:
        raise ActivityError("Recent activity is temporarily unavailable.")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def format_live_event(event: dict[str, Any], *, resource_title: str = "") -> dict[str, Any]:
    event_type = event.get("event_type", "")
    label = event_type.replace("_", " ").title()
    detail = resource_title
    if event_type == "catalog_search":
        detail = event.get("search_query") or detail
    elif event.get("resource_id") and not detail:
        detail = f"Resource {str(event.get('resource_id'))[:8]}…"
    return {
        "event_id": event.get("event_id"),
        "event_type": event_type,
        "label": label,
        "detail": detail or event.get("occurred_at", "")[:16].replace("T", " "),
        "occurred_at": event.get("occurred_at"),
        "meaningful": event_type in MEANINGFUL_EVENT_TYPES,
    }


def count_meaningful_events(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event.get("event_type") in MEANINGFUL_EVENT_TYPES)


def batch_has_meaningful_events(batch: ActivityBatch) -> bool:
    return any(event.event_type in MEANINGFUL_EVENT_TYPES for event in batch.events)