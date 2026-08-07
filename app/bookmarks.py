from __future__ import annotations

import httpx

from app.config import settings

BOOKMARK_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class BookmarkError(RuntimeError):
    """Raised when saved resources cannot be loaded."""


async def bookmarked_product_ids(
    access_token: str,
    user_id: str,
    limit: int = 50,
) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=BOOKMARK_TIMEOUT) as client:
            response = await client.get(
                f"{settings.supabase_url}/rest/v1/activity_events",
                headers={
                    "apikey": settings.supabase_anon_key,
                    "Authorization": f"Bearer {access_token}",
                },
                params={
                    "select": "resource_id,occurred_at",
                    "user_id": f"eq.{user_id}",
                    "event_type": "eq.bookmark_added",
                    "resource_id": "not.is.null",
                    "order": "occurred_at.desc",
                    "limit": str(max(1, min(limit, 100))),
                },
            )
    except httpx.HTTPError as error:
        raise BookmarkError("Bookmarks are temporarily unavailable.") from error
    if response.is_error:
        raise BookmarkError("Bookmarks are temporarily unavailable.")
    rows = response.json()
    if not isinstance(rows, list):
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        resource_id = row.get("resource_id")
        if resource_id and resource_id not in seen:
            seen.add(resource_id)
            ordered.append(resource_id)
    return ordered
