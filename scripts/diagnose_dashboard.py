#!/usr/bin/env python3
"""Diagnose dashboard data fetches on live."""

import asyncio
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = os.environ.get("LIVE_BASE_URL", "https://v-1-ora9.onrender.com")
EMAIL = os.environ.get("LIVE_TEST_EMAIL", "954954@sendora.me")
PASSWORD = os.environ.get("LIVE_TEST_PASSWORD", "954954@sendora.me")


async def main() -> None:
    from app.auth import resolve_access_token
    from app.activity import recent_events
    from app.bookmarks import bookmarked_product_ids
    from app.catalog import list_products_for_goal
    from app.interest import get_interest_profile
    from app.progress import learning_streak, list_progress, weekly_learning_minutes
    from app.recommendations import latest_recommendation, recommendation_history

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0, follow_redirects=False) as client:
        res = await client.post("/auth/login", data={"email": EMAIL, "password": PASSWORD})
        print("login", res.status_code, res.headers.get("location"))
        cookies = client.cookies
        class Req:
            def __init__(self):
                self.cookies = cookies
                self.state = type("S", (), {})()
        request = Req()
        token, user = await resolve_access_token(request)  # type: ignore[arg-type]
        print("token", bool(token), user.get("email") if user else None)
        if not token or not user:
            return
        uid = user["id"]
        goal = "AI Engineer"
        for name, coro in [
            ("recent_events", recent_events(token, uid, limit=12)),
            ("list_products_for_goal", list_products_for_goal(goal)),
            ("list_progress", list_progress(token, uid)),
            ("learning_streak", learning_streak(token, uid)),
            ("weekly_minutes", weekly_learning_minutes(token, uid)),
            ("interest_profile", get_interest_profile(token, uid)),
            ("latest_recommendation", latest_recommendation(token, uid)),
            ("history", recommendation_history(token, uid)),
        ]:
            try:
                result = await coro
                summary = len(result) if isinstance(result, list) else type(result).__name__
                print(f"OK  {name}: {summary}")
            except Exception as error:
                print(f"ERR {name}: {type(error).__name__}: {error}")


if __name__ == "__main__":
    asyncio.run(main())
