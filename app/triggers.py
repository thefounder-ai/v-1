from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

RECOMMENDATION_COOLDOWN_SECONDS = 300
RECOMMENDATION_TTL_HOURS = 24


def recommendation_expires_at(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    expiry = moment + timedelta(hours=RECOMMENDATION_TTL_HOURS)
    return expiry.replace(microsecond=0).isoformat()


def is_recommendation_fresh(recommendation: dict[str, Any] | None) -> bool:
    if not recommendation:
        return False
    expires_at = recommendation.get("expires_at")
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    return expiry > datetime.now(timezone.utc)


def within_cooldown(recommendation: dict[str, Any] | None) -> bool:
    if not recommendation or not recommendation.get("created_at"):
        return False
    created_at = datetime.fromisoformat(
        str(recommendation["created_at"]).replace("Z", "+00:00")
    )
    age = (datetime.now(timezone.utc) - created_at).total_seconds()
    return age < RECOMMENDATION_COOLDOWN_SECONDS


def should_auto_generate(
    interest_profile: dict[str, Any] | None,
    latest: dict[str, Any] | None,
) -> bool:
    if not interest_profile or not interest_profile.get("refresh_recommended"):
        return False
    if latest and within_cooldown(latest) and is_recommendation_fresh(latest):
        return False
    if latest and is_recommendation_fresh(latest):
        return False
    return True
