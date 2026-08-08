from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

import httpx

from app.catalog import CatalogError, list_products_by_ids
from app.config import settings
from app.recommendations import RecommendationError, latest_recommendation

EMAIL_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class EmailDeliveryError(RuntimeError):
    """Raised when an explicit recommendation email cannot be delivered."""


def _email_headers(access_token: str) -> dict[str, str]:
    if not settings.supabase_configured:
        raise EmailDeliveryError("Supabase is not configured.")
    return {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _resend_headers() -> dict[str, str]:
    if not settings.resend_api_key:
        raise EmailDeliveryError("Email delivery is not configured yet.")
    return {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }


def recommendation_email_html(
    recommendation: dict[str, Any],
    recipient_name: str = "there",
) -> str:
    title = html.escape(str(recommendation.get("summary") or "Your next learning step"))
    next_step = html.escape(str(recommendation.get("next_step") or "Open your SkillOrbit dashboard."))
    items = []
    for item in recommendation.get("items") or []:
        items.append(
            "<li style='margin:0 0 16px'>"
            f"<strong>{html.escape(str(item.get('title') or 'Learning resource'))}</strong><br>"
            f"<span>{html.escape(str(item.get('reason') or 'Grounded in your learning signals.'))}</span>"
            "</li>"
        )
    item_markup = "".join(items) or "<li>Your grounded path is ready in SkillOrbit.</li>"
    return f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#202a2e;line-height:1.6">
<div style="max-width:620px;margin:0 auto;padding:32px 20px">
<p style="color:#e9784c;font-size:12px;letter-spacing:.12em;text-transform:uppercase">SkillOrbit</p>
<h1 style="color:#2d5a4b;font-size:28px;line-height:1.2">Your next learning step</h1>
<p>Hi {html.escape(recipient_name)},</p>
<p>{title}</p>
<div style="border-left:3px solid #e9784c;padding:12px 16px;background:#f5f6f2">
<strong>Next best step</strong><br>{next_step}
</div>
<h2 style="font-size:18px">Grounded resources</h2>
<ol>{item_markup}</ol>
<p style="margin-top:32px"><a href="{html.escape(settings.app_public_url)}"
style="display:inline-block;background:#2d5a4b;color:#fff;padding:12px 18px;text-decoration:none">
Open SkillOrbit</a></p>
</div></body></html>"""


async def _delivery_log(
    access_token: str,
    user_id: str,
    recommendation_id: str,
    recipient_email: str,
    *,
    status: str,
    error_message: str | None = None,
    provider_message_id: str | None = None,
    delivery_id: str | None = None,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "user_id": user_id,
        "recommendation_id": recommendation_id,
        "recipient_email": recipient_email,
        "status": status,
        "provider": "resend",
        "delivery_kind": "manual",
        "error_message": error_message,
        "provider_message_id": provider_message_id,
    }
    if status == "sent":
        payload["sent_at"] = datetime.now(timezone.utc).isoformat()
    method = "PATCH" if delivery_id else "POST"
    path = "/rest/v1/email_deliveries"
    params = [("id", f"eq.{delivery_id}")] if delivery_id else []
    headers = {**_email_headers(access_token), "Prefer": "return=representation"}
    try:
        async with httpx.AsyncClient(timeout=EMAIL_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{settings.supabase_url}{path}",
                headers=headers,
                params=params,
                json=payload,
            )
    except httpx.HTTPError as error:
        raise EmailDeliveryError("Delivery status could not be saved.") from error
    if response.is_error:
        raise EmailDeliveryError("Delivery status could not be saved.")
    rows = response.json()
    return rows[0] if isinstance(rows, list) and rows else None


async def send_recommendation_email(
    access_token: str,
    user_id: str,
    recipient_email: str,
    recommendation_id: str,
    recipient_name: str = "there",
) -> dict[str, Any]:
    try:
        recommendation = await latest_recommendation(access_token, user_id)
    except RecommendationError as error:
        raise EmailDeliveryError("The recommendation is temporarily unavailable.") from error
    if not recommendation or recommendation.get("id") != recommendation_id:
        raise EmailDeliveryError("That recommendation is not available for email.")
    item_ids = [item["product_id"] for item in recommendation.get("items") or []]
    try:
        products = await list_products_by_ids(item_ids)
    except CatalogError as error:
        raise EmailDeliveryError("Recommended resources are temporarily unavailable.") from error
    product_map = {product["id"]: product for product in products}
    recommendation["items"] = [
        {
            **item,
            "title": product_map.get(item["product_id"], {}).get("title", "Learning resource"),
        }
        for item in recommendation.get("items") or []
        if item.get("product_id") in product_map
    ]
    pending = await _delivery_log(
        access_token,
        user_id,
        recommendation_id,
        recipient_email,
        status="pending",
    )
    try:
        payload = {
            "from": settings.resend_from_email,
            "to": [recipient_email],
            "subject": "Your next learning step from SkillOrbit",
            "html": recommendation_email_html(recommendation, recipient_name),
        }
        async with httpx.AsyncClient(timeout=EMAIL_TIMEOUT) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers=_resend_headers(),
                json=payload,
            )
        if response.is_error:
            raise EmailDeliveryError("Resend could not deliver this email.")
        provider_id = response.json().get("id")
        saved = await _delivery_log(
            access_token,
            user_id,
            recommendation_id,
            recipient_email,
            status="sent",
            provider_message_id=provider_id,
            delivery_id=pending.get("id") if pending else None,
        )
        return {"status": "sent", "delivery_id": saved.get("id") if saved else None}
    except (httpx.HTTPError, EmailDeliveryError) as error:
        message = str(error)
        try:
            await _delivery_log(
                access_token,
                user_id,
                recommendation_id,
                recipient_email,
                status="failed",
                error_message=message,
                delivery_id=pending.get("id") if pending else None,
            )
        except EmailDeliveryError:
            pass
        raise EmailDeliveryError(message) from error