from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings
from app.email_delivery import recommendation_email_html
from app.observability import event_logger, log_event

logger = event_logger("skillorbit.digest")
DIGEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class DigestError(RuntimeError):
    """Raised when batch digest delivery fails."""


def _service_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    if not key:
        raise DigestError("Service role key is not configured.")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _resend_headers() -> dict[str, str]:
    if not settings.resend_api_key:
        raise DigestError("Resend is not configured.")
    return {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }


async def _recent_active_user_ids(since: datetime) -> list[str]:
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/rest/v1/activity_events",
            headers=_service_headers(),
            params={
                "select": "user_id",
                "occurred_at": f"gte.{since.isoformat()}",
                "order": "occurred_at.desc",
                "limit": "500",
            },
        )
    if response.is_error:
        raise DigestError("Could not load recent activity.")
    rows = response.json()
    if not isinstance(rows, list):
        return []
    seen: set[str] = set()
    user_ids: list[str] = []
    for row in rows:
        user_id = row.get("user_id")
        if user_id and user_id not in seen:
            seen.add(user_id)
            user_ids.append(user_id)
    return user_ids


async def _profile_email(user_id: str) -> tuple[str, str] | None:
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        profile_response = await client.get(
            f"{settings.supabase_url}/rest/v1/profiles",
            headers=_service_headers(),
            params={
                "select": "user_id,career_goal,onboarding_complete",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
    if profile_response.is_error:
        return None
    profiles = profile_response.json()
    if not profiles or not profiles[0].get("onboarding_complete"):
        return None

    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        user_response = await client.get(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            headers=_service_headers(),
        )
    if user_response.is_error:
        return None
    user = user_response.json()
    email = user.get("email")
    if not email:
        return None
    goal = profiles[0].get("career_goal") or ""
    name = goal if goal else email.split("@", 1)[0]
    return email, name


async def _latest_recommendation(user_id: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/rest/v1/recommendations",
            headers=_service_headers(),
            params={
                "select": "id,summary,next_step,model,trigger_event_count,status,created_at",
                "user_id": f"eq.{user_id}",
                "status": "eq.active",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
    if response.is_error:
        return None
    rows = response.json()
    if not rows:
        return None
    recommendation = rows[0]
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        items_response = await client.get(
            f"{settings.supabase_url}/rest/v1/recommendation_items",
            headers=_service_headers(),
            params={
                "select": "product_id,rank,reason",
                "recommendation_id": f"eq.{recommendation['id']}",
                "order": "rank.asc",
            },
        )
    if items_response.is_error:
        return recommendation
    item_rows = items_response.json()
    if not isinstance(item_rows, list) or not item_rows:
        recommendation["items"] = []
        return recommendation
    product_ids = [row.get("product_id") for row in item_rows if row.get("product_id")]
    titles: dict[str, str] = {}
    if product_ids:
        async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
            products_response = await client.get(
                f"{settings.supabase_url}/rest/v1/products",
                headers=_service_headers(),
                params={
                    "select": "id,title",
                    "id": f"in.({','.join(product_ids)})",
                },
            )
        if not products_response.is_error:
            for product in products_response.json() or []:
                titles[product["id"]] = product.get("title", "Learning resource")
    recommendation["items"] = [
        {
            "title": titles.get(row.get("product_id"), "Learning resource"),
            "reason": row.get("reason", ""),
        }
        for row in item_rows
    ]
    return recommendation


async def _digest_sent_today(user_id: str) -> bool:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/rest/v1/email_deliveries",
            headers=_service_headers(),
            params={
                "select": "id",
                "user_id": f"eq.{user_id}",
                "status": "eq.sent",
                "sent_at": f"gte.{start.isoformat()}",
                "limit": "1",
            },
        )
    if response.is_error:
        return False
    rows = response.json()
    return bool(rows)


async def _send_digest_email(
    user_id: str,
    recipient_email: str,
    recipient_name: str,
    recommendation: dict[str, Any],
) -> None:
    pending_payload = {
        "user_id": user_id,
        "recommendation_id": recommendation["id"],
        "recipient_email": recipient_email,
        "status": "pending",
        "provider": "resend",
    }
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        pending = await client.post(
            f"{settings.supabase_url}/rest/v1/email_deliveries",
            headers={**_service_headers(), "Prefer": "return=representation"},
            json=pending_payload,
        )
    delivery_id = None
    if not pending.is_error:
        rows = pending.json()
        if rows:
            delivery_id = rows[0].get("id")

    payload = {
        "from": settings.resend_from_email,
        "to": [recipient_email],
        "subject": "Your weekly learning digest from SkillOrbit",
        "html": recommendation_email_html(recommendation, recipient_name),
    }
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        send_response = await client.post(
            "https://api.resend.com/emails",
            headers=_resend_headers(),
            json=payload,
        )
    status = "sent" if not send_response.is_error else "failed"
    update_payload: dict[str, Any] = {
        "status": status,
        "provider_message_id": send_response.json().get("id") if not send_response.is_error else None,
        "error_message": None if not send_response.is_error else send_response.text[:500],
    }
    if status == "sent":
        update_payload["sent_at"] = datetime.now(timezone.utc).isoformat()
    if delivery_id:
        async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
            await client.patch(
                f"{settings.supabase_url}/rest/v1/email_deliveries",
                headers=_service_headers(),
                params={"id": f"eq.{delivery_id}"},
                json=update_payload,
            )


async def run_weekly_digest() -> dict[str, int]:
    """Send at most one digest email per user per day when they have activity + an active path."""
    if not settings.resend_configured or not settings.supabase_service_role_key:
        log_event(logger, logging.INFO, "digest_skipped", reason="not_configured")
        return {"sent": 0, "skipped": 0, "failed": 0}

    since = datetime.now(timezone.utc) - timedelta(days=7)
    sent = skipped = failed = 0
    try:
        user_ids = await _recent_active_user_ids(since)
    except DigestError as error:
        log_event(logger, logging.WARNING, "digest_failed", error=str(error))
        return {"sent": 0, "skipped": 0, "failed": 0}

    for user_id in user_ids:
        try:
            if await _digest_sent_today(user_id):
                skipped += 1
                continue
            identity = await _profile_email(user_id)
            if not identity:
                skipped += 1
                continue
            email, name = identity
            recommendation = await _latest_recommendation(user_id)
            if not recommendation:
                skipped += 1
                continue
            await _send_digest_email(user_id, email, name, recommendation)
            sent += 1
        except (DigestError, httpx.HTTPError) as error:
            failed += 1
            log_event(logger, logging.WARNING, "digest_user_failed", user_id=user_id, error=str(error))

    log_event(logger, logging.INFO, "digest_finished", sent=sent, skipped=skipped, failed=failed)
    return {"sent": sent, "skipped": skipped, "failed": failed}
