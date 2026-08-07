from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

import httpx

from app.config import settings

PROGRESS_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
ProgressStatus = Literal["started", "completed"]


class ProgressError(RuntimeError):
    """Raised when learner progress cannot be read or saved."""


def _headers(access_token: str, prefer: str | None = None) -> dict[str, str]:
    if not settings.supabase_configured:
        raise ProgressError("Supabase is not configured.")
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def list_progress(access_token: str, user_id: str) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=PROGRESS_TIMEOUT) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/user_progress",
                headers=_headers(access_token),
                params={
                    "select": "product_id,status,updated_at",
                    "user_id": f"eq.{user_id}",
                    "order": "updated_at.desc",
                },
            )
    except httpx.HTTPError as error:
        raise ProgressError("Progress is temporarily unavailable.") from error
    if response.is_error:
        raise ProgressError("Progress is temporarily unavailable.")
    rows = response.json()
    return rows if isinstance(rows, list) else []


async def set_progress(
    access_token: str,
    user_id: str,
    product_id: str,
    status: ProgressStatus,
) -> dict[str, Any]:
    payload = {
        "user_id": user_id,
        "product_id": product_id,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=PROGRESS_TIMEOUT) as client:
            response = await client.post(
                f"{settings.supabase_url}/rest/v1/user_progress",
                headers=_headers(access_token, prefer="resolution=merge-duplicates,return=representation"),
                params={"on_conflict": "user_id,product_id"},
                json=payload,
            )
    except httpx.HTTPError as error:
        raise ProgressError("Progress could not be saved.") from error
    if response.is_error:
        raise ProgressError("Progress could not be saved.")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else payload


def progress_summary(
    progress_rows: list[dict[str, Any]],
    path_product_ids: list[str],
) -> dict[str, Any]:
    completed = {row["product_id"] for row in progress_rows if row.get("status") == "completed"}
    started = {row["product_id"] for row in progress_rows if row.get("status") == "started"}
    path_total = len(path_product_ids)
    path_completed = sum(1 for product_id in path_product_ids if product_id in completed)
    percent = round((path_completed / path_total) * 100) if path_total else 0
    return {
        "completed_count": len(completed),
        "started_count": len(started),
        "path_total": path_total,
        "path_completed": path_completed,
        "path_percent": percent,
    }


async def learning_streak(access_token: str, user_id: str) -> int:
    """Count consecutive calendar days with meaningful learning activity."""
    since = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    try:
        async with httpx.AsyncClient(timeout=PROGRESS_TIMEOUT) as client:
            events_response = await client.get(
                f"{settings.supabase_url}/rest/v1/activity_events",
                headers={
                    "apikey": settings.supabase_anon_key,
                    "Authorization": f"Bearer {access_token}",
                },
                params={
                    "select": "occurred_at,event_type",
                    "user_id": f"eq.{user_id}",
                    "occurred_at": f"gte.{since}",
                    "order": "occurred_at.desc",
                    "limit": "300",
                },
            )
            progress_response = await client.get(
                f"{settings.supabase_url}/rest/v1/user_progress",
                headers=_headers(access_token),
                params={
                    "select": "updated_at,status",
                    "user_id": f"eq.{user_id}",
                    "updated_at": f"gte.{since}",
                    "order": "updated_at.desc",
                    "limit": "100",
                },
            )
    except httpx.HTTPError as error:
        raise ProgressError("Learning streak could not be calculated.") from error
    if events_response.is_error or progress_response.is_error:
        raise ProgressError("Learning streak could not be calculated.")

    active_days: set[str] = set()
    meaningful = {
        "resource_view",
        "resource_dwell",
        "bookmark_added",
        "catalog_search",
        "recommendation_opened",
    }
    for row in events_response.json() or []:
        if row.get("event_type") in meaningful and row.get("occurred_at"):
            active_days.add(str(row["occurred_at"])[:10])
    for row in progress_response.json() or []:
        if row.get("updated_at"):
            active_days.add(str(row["updated_at"])[:10])

    streak = 0
    today = date.today()
    for offset in range(365):
        day = today - timedelta(days=offset)
        if day.isoformat() in active_days:
            streak += 1
        elif offset > 0:
            break
    return streak


async def weekly_learning_minutes(access_token: str, user_id: str) -> int:
    """Sum dwell-time and completed-resource minutes over the last 7 days."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        async with httpx.AsyncClient(timeout=PROGRESS_TIMEOUT) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/activity_events",
                headers={
                    "apikey": settings.supabase_anon_key,
                    "Authorization": f"Bearer {access_token}",
                },
                params={
                    "select": "event_type,duration_seconds,resource_id",
                    "user_id": f"eq.{user_id}",
                    "occurred_at": f"gte.{since}",
                    "limit": "500",
                },
            )
    except httpx.HTTPError as error:
        raise ProgressError("Weekly learning time could not be calculated.") from error
    if response.is_error:
        raise ProgressError("Weekly learning time could not be calculated.")

    total_seconds = 0
    for row in response.json() or []:
        if row.get("event_type") == "resource_dwell":
            total_seconds += int(row.get("duration_seconds") or 0)
    return max(0, total_seconds // 60)
