from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.auth import get_profile
from app.config import settings
from app.email_delivery import recommendation_email_html
from app.interest import InterestProfileError, get_interest_profile, refresh_interest_profile
from app.observability import event_logger, log_event
from app.recommendations import RecommendationError, generate_recommendation
from app.triggers import is_recommendation_fresh

logger = event_logger("skillorbit.digest")
DIGEST_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
WEEKLY_DIGEST_KIND = "weekly_digest"


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


def _service_token() -> str:
    token = settings.supabase_service_role_key
    if not token:
        raise DigestError("Service role key is not configured.")
    return token


def _resend_headers() -> dict[str, str]:
    if not settings.resend_api_key:
        raise DigestError("Resend is not configured.")
    return {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def weekly_digest_due(
    *,
    now: datetime,
    account_anchor: datetime,
    last_digest_sent: datetime | None,
    interval_days: int,
) -> bool:
    """First digest after interval_days from onboarding; then every interval_days."""
    if last_digest_sent:
        return now - last_digest_sent >= timedelta(days=interval_days)
    return now - account_anchor >= timedelta(days=interval_days)


async def _onboarded_digest_candidates() -> list[dict[str, Any]]:
    """All learners who finished onboarding (path not required)."""
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/rest/v1/profiles",
            headers=_service_headers(),
            params={
                "select": "user_id,career_goal,weekly_minutes,updated_at,created_at",
                "onboarding_complete": "eq.true",
                "limit": "500",
            },
        )
    if response.is_error:
        raise DigestError("Could not load learner profiles.")
    rows = response.json()
    return rows if isinstance(rows, list) else []


async def _profile_email(user_id: str) -> tuple[str, str] | None:
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
    profile = await get_profile(_service_token(), user_id)
    goal = (profile or {}).get("career_goal") or ""
    name = goal if goal else email.split("@", 1)[0]
    return email, name


def _learner_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "career_goal": profile.get("career_goal"),
        "weekly_minutes": profile.get("weekly_minutes"),
    }


async def _latest_recommendation(user_id: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/rest/v1/recommendations",
            headers=_service_headers(),
            params={
                "select": "id,summary,next_step,model,trigger_event_count,status,created_at,expires_at",
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
    if not is_recommendation_fresh(recommendation):
        return None
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


def path_needs_refresh(
    *,
    recommendation: dict[str, Any] | None,
    refresh_recommended: bool,
    last_digest_sent: datetime | None,
) -> bool:
    """True when email day should trigger a new LangGraph path before sending."""
    if not recommendation:
        return True
    if not is_recommendation_fresh(recommendation):
        return True
    if refresh_recommended:
        return True
    created = _parse_timestamp(recommendation.get("created_at"))
    if last_digest_sent and created and created <= last_digest_sent:
        return True
    return False


async def _ensure_latest_recommendation_for_digest(
    user_id: str,
    profile: dict[str, Any],
    *,
    last_digest_sent: datetime | None,
) -> tuple[dict[str, Any], bool]:
    """On email day: reuse a fresh path or auto-generate the latest one."""
    token = _service_token()
    learner = _learner_from_profile(profile)
    existing = await _latest_recommendation(user_id)
    refresh_recommended = False
    try:
        await refresh_interest_profile(token, user_id, profile)
        interest = await get_interest_profile(token, user_id)
        refresh_recommended = bool(interest and interest.get("refresh_recommended"))
    except InterestProfileError:
        log_event(logger, logging.INFO, "digest_profile_refresh_skipped", user_id=user_id)

    if existing and not path_needs_refresh(
        recommendation=existing,
        refresh_recommended=refresh_recommended,
        last_digest_sent=last_digest_sent,
    ):
        log_event(logger, logging.INFO, "digest_using_existing_path", user_id=user_id)
        return existing, False

    reason = "no_active_path" if not existing else "path_not_latest"
    log_event(logger, logging.INFO, "digest_generating_path", user_id=user_id, reason=reason)

    try:
        await generate_recommendation(token, user_id, learner)
    except RecommendationError as error:
        raise DigestError(str(error)) from error

    recommendation = await _latest_recommendation(user_id)
    if not recommendation:
        raise DigestError("A learning path could not be prepared for the weekly digest.")
    return recommendation, True


async def _last_weekly_digest_sent(user_id: str) -> datetime | None:
    async with httpx.AsyncClient(timeout=DIGEST_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/rest/v1/email_deliveries",
            headers=_service_headers(),
            params={
                "select": "sent_at",
                "user_id": f"eq.{user_id}",
                "status": "eq.sent",
                "delivery_kind": f"eq.{WEEKLY_DIGEST_KIND}",
                "order": "sent_at.desc",
                "limit": "1",
            },
        )
    if response.is_error:
        return None
    rows = response.json()
    if not rows:
        return None
    return _parse_timestamp(rows[0].get("sent_at"))


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
        "delivery_kind": WEEKLY_DIGEST_KIND,
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
        "subject": "Your weekly learning path from SkillOrbit",
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
    if status != "sent":
        raise DigestError("Resend could not deliver the weekly digest.")


async def run_weekly_digest() -> dict[str, int]:
    """Weekly proactive email: auto-generate latest path if needed, then send."""
    if not settings.digest_configured:
        log_event(logger, logging.INFO, "digest_skipped", reason="not_configured")
        return {"sent": 0, "skipped": 0, "failed": 0, "due": 0, "generated": 0}

    now = datetime.now(timezone.utc)
    interval_days = max(1, settings.digest_interval_days)
    sent = skipped = failed = due = generated = 0
    try:
        candidates = await _onboarded_digest_candidates()
    except DigestError as error:
        log_event(logger, logging.WARNING, "digest_failed", error=str(error))
        return {"sent": 0, "skipped": 0, "failed": 0, "due": 0, "generated": 0}

    for profile in candidates:
        user_id = profile.get("user_id")
        if not user_id:
            skipped += 1
            continue
        account_anchor = _parse_timestamp(profile.get("updated_at")) or _parse_timestamp(profile.get("created_at"))
        if not account_anchor:
            skipped += 1
            continue
        try:
            last_sent = await _last_weekly_digest_sent(user_id)
            if not weekly_digest_due(
                now=now,
                account_anchor=account_anchor,
                last_digest_sent=last_sent,
                interval_days=interval_days,
            ):
                skipped += 1
                continue
            due += 1
            identity = await _profile_email(user_id)
            if not identity:
                skipped += 1
                continue
            email, name = identity
            recommendation, was_generated = await _ensure_latest_recommendation_for_digest(
                user_id,
                profile,
                last_digest_sent=last_sent,
            )
            if was_generated:
                generated += 1
            await _send_digest_email(user_id, email, name, recommendation)
            sent += 1
            log_event(
                logger,
                logging.INFO,
                "weekly_digest_sent",
                user_id=user_id,
                recipient=email,
                recommendation_id=recommendation.get("id"),
            )
        except (DigestError, RecommendationError, httpx.HTTPError) as error:
            failed += 1
            log_event(logger, logging.WARNING, "digest_user_failed", user_id=user_id, error=str(error))

    log_event(
        logger,
        logging.INFO,
        "digest_finished",
        sent=sent,
        skipped=skipped,
        failed=failed,
        due=due,
        generated=generated,
        interval_days=interval_days,
    )
    return {"sent": sent, "skipped": skipped, "failed": failed, "due": due, "generated": generated}
