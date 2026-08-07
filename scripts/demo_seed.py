#!/usr/bin/env python3
"""Seed demo learner activity for reliable judge demos.

Requires SUPABASE_SERVICE_ROLE_KEY and DEMO_USER_EMAIL in the environment.
Optionally set DEMO_USER_PASSWORD if the account must be created first.

Usage:
  python scripts/demo_seed.py
  python scripts/demo_seed.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.catalog import list_products  # noqa: E402

TIMEOUT = httpx.Timeout(30.0, connect=5.0)

DEMO_STEPS = """
SkillOrbit — 60 second judge demo
=================================
1. Open /explore (public) → search "production RAG"
2. Sign up → onboarding → pick "AI Engineer"
3. Open 2 resources, dwell 30s+, bookmark one
4. Dashboard → show interest radar + activity feed
5. Generate path → show grounded recommendation + evidence
6. Browse different topic → Refresh path → show "What changed" diff
7. Admin → add resource → Index pending → appears in search

To pre-seed activity for a demo account:
  set DEMO_USER_EMAIL=demo@example.com
  python scripts/demo_seed.py --apply

GitHub secrets required: MESH_API_KEY, SUBMISSION_TOKEN
Migrations: 001 through 014 in Supabase SQL editor.
After seeding catalog: python scripts/bootstrap_qdrant.py
"""


def _service_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    if not key:
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY is required for --apply.")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def _find_user_id(email: str) -> str | None:
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


async def _ensure_profile(user_id: str) -> None:
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


async def _seed_events(user_id: str, product_ids: list[str]) -> int:
    now = datetime.now(timezone.utc)
    events: list[dict] = []
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


async def apply_seed() -> None:
    email = os.environ.get("DEMO_USER_EMAIL", "").strip()
    if not email:
        raise SystemExit("Set DEMO_USER_EMAIL to the demo learner account email.")

    user_id = await _find_user_id(email)
    if not user_id:
        raise SystemExit(f"No Supabase user found for {email}. Create the account first.")

    products = await list_products(career_goal="AI Engineer")
    rag_products = [
        product for product in products
        if "rag" in (product.get("title") or "").lower()
        or "rag" in (product.get("short_summary") or "").lower()
    ]
    product_ids = [product["id"] for product in (rag_products or products)[:6]]
    await _ensure_profile(user_id)
    count = await _seed_events(user_id, product_ids)
    print(f"Seeded {count} activity events for {email} ({user_id}).")
    print("Next: sign in as this user → Dashboard → Refresh path → show diff.")


async def main_async(apply: bool) -> None:
    if apply:
        await apply_seed()
    else:
        print(DEMO_STEPS.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="SkillOrbit demo helper")
    parser.add_argument("--apply", action="store_true", help="Insert demo activity events")
    args = parser.parse_args()
    asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    main()
