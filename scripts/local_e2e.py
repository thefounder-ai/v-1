#!/usr/bin/env python3
"""Authenticated end-to-end flow — run while uvicorn is on :5000."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import list_products  # noqa: E402

BASE = "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def ok(label: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


async def main() -> None:
    email = os.environ.get("E2E_TEST_EMAIL", "").strip()
    password = os.environ.get("E2E_TEST_PASSWORD", "").strip()
    signup_mode = not (email and password)
    if signup_mode:
        email = f"skillorbit.e2e.{int(time.time())}@mailinator.com"
        password = "LocalTest123!"

    print("SkillOrbit authenticated E2E")
    print("=" * 50)
    print(f"Test user: {email}" + (" (signup)" if signup_mode else " (login)"))
    print()

    async with httpx.AsyncClient(base_url=BASE, timeout=120.0, follow_redirects=False) as client:
        if signup_mode:
            res = await client.post(
                "/auth/signup",
                data={"email": email, "password": password},
            )
            if res.status_code == 303 and "/onboarding" in (res.headers.get("location") or ""):
                ok("Signup", "redirected to onboarding")
            elif res.status_code == 303 and "/login" in (res.headers.get("location") or ""):
                res = await client.post("/auth/login", data={"email": email, "password": password})
                if res.status_code == 303:
                    ok("Signup + login", "email confirmation required, logged in")
                else:
                    bad("Signup", f"login fallback status={res.status_code}")
                    return
            else:
                loc = res.headers.get("location") or ""
                if "rate%20limit" in loc or "rate limit" in loc.lower():
                    bad(
                        "Signup",
                        "Supabase email rate limit — set E2E_TEST_EMAIL and E2E_TEST_PASSWORD env vars",
                    )
                else:
                    bad("Signup", f"status={res.status_code} loc={loc}")
                return
        else:
            res = await client.post("/auth/login", data={"email": email, "password": password})
            if res.status_code != 303:
                bad("Login", f"status={res.status_code} loc={res.headers.get('location')}")
                return
            loc = res.headers.get("location") or ""
            if "/onboarding" in loc:
                ok("Login", "needs onboarding")
            else:
                ok("Login", f"redirected to {loc}")
        res = await client.post(
            "/onboarding",
            data={
                "career_goal": "Generative AI Builder",
                "current_level": "Intermediate",
                "weekly_minutes": "300",
            },
        )
        if res.status_code == 303 and "/dashboard" in (res.headers.get("location") or ""):
            ok("Onboarding", "career goal saved")
        elif res.status_code == 303 and "/onboarding" in (res.headers.get("location") or ""):
            bad("Onboarding", "validation failed")
            return
        else:
            # Already onboarded users skip to dashboard via login redirect
            dash = await client.get("/dashboard")
            if dash.status_code == 200:
                ok("Onboarding", "already complete")
            else:
                bad("Onboarding", f"status={res.status_code} loc={res.headers.get('location')}")
                return

        products = await list_products()
        if not products:
            bad("Catalog lookup", "no products")
            return
        product_id = products[0]["id"]
        product_title = products[0].get("title", product_id)[:40]
        ok("Catalog lookup", product_title)

        # Resource page
        res = await client.get(f"/resource/{product_id}")
        if res.status_code == 200 and product_id in res.text:
            ok("GET /resource/{id}", "detail page loads")
        else:
            bad("GET /resource/{id}", f"status={res.status_code}")

        # Bookmark via events API
        event_payload = {
            "events": [{
                "event_id": str(uuid4()),
                "event_type": "bookmark_added",
                "resource_id": product_id,
            }]
        }
        res = await client.post("/api/events", json=event_payload)
        if res.status_code == 200 and res.json().get("accepted", 0) >= 1:
            ok("POST /api/events bookmark", "accepted")
        else:
            bad("POST /api/events bookmark", f"status={res.status_code} body={res.text[:200]}")

        # Search + dwell for interest signals
        search_payload = {
            "events": [{
                "event_id": str(uuid4()),
                "event_type": "catalog_search",
                "search_query": "production RAG systems",
            }]
        }
        res = await client.post("/api/events", json=search_payload)
        if res.status_code == 200:
            ok("POST /api/events search", "accepted")
        else:
            bad("POST /api/events search", str(res.status_code))

        dwell_payload = {
            "events": [{
                "event_id": str(uuid4()),
                "event_type": "resource_dwell",
                "resource_id": product_id,
                "duration_seconds": 45,
            }]
        }
        res = await client.post("/api/events", json=dwell_payload)
        if res.status_code == 200:
            ok("POST /api/events dwell", "accepted")
        else:
            bad("POST /api/events dwell", str(res.status_code))

        # Progress
        res = await client.post(
            f"/api/progress/{product_id}",
            data={"progress_status": "completed"},
        )
        if res.status_code == 200 and res.json().get("status") == "completed":
            ok("POST /api/progress", "marked completed")
        else:
            bad("POST /api/progress", f"status={res.status_code} body={res.text[:200]}")

        # Bookmarks page
        res = await client.get("/bookmarks")
        if res.status_code == 200 and product_id in res.text:
            ok("GET /bookmarks", "saved resource visible")
        else:
            bad("GET /bookmarks", f"status={res.status_code}, product in page={product_id in res.text}")

        # Dashboard
        res = await client.get("/dashboard")
        if res.status_code == 200:
            body = res.text
            checks = [
                ("skill radar", "skill-radar" in body or "radar" in body.lower()),
                ("activity feed", "activity" in body.lower()),
                ("progress", "path" in body.lower() or "progress" in body.lower()),
            ]
            for name, present in checks:
                if present:
                    ok(f"Dashboard {name}", "rendered")
                else:
                    bad(f"Dashboard {name}", "not found in HTML")
        else:
            bad("GET /dashboard", f"status={res.status_code}")

        # Interest profile refresh
        res = await client.post("/api/interest-profile/refresh")
        if res.status_code == 200:
            data = res.json()
            ok(
                "POST /api/interest-profile/refresh",
                f"events={data.get('meaningful_event_count', '?')}",
            )
        else:
            bad("POST /api/interest-profile/refresh", f"status={res.status_code}")

        # AI recommendation (may take 30-90s)
        print("  ... generating recommendation (Mesh + Qdrant, up to 120s)")
        res = await client.post("/api/recommendations/generate?force=true")
        if res.status_code == 200:
            data = res.json()
            items = data.get("items") or []
            summary = (data.get("summary") or "")[:80]
            if items and summary:
                ok(
                    "POST /api/recommendations/generate",
                    f"{len(items)} items, cached={data.get('cached')}, trace={bool(data.get('trace_id'))}",
                )
            else:
                bad("POST /api/recommendations/generate", "empty items or summary")
        else:
            bad(
                "POST /api/recommendations/generate",
                f"status={res.status_code} body={res.text[:300]}",
            )

        # Recommendations page
        res = await client.get("/recommendations")
        if res.status_code == 200 and len(res.text) > 500:
            ok("GET /recommendations", f"{len(res.text)} bytes")
        else:
            bad("GET /recommendations", f"status={res.status_code}")

        # Learning path
        res = await client.get("/learning-path")
        if res.status_code == 200:
            ok("GET /learning-path", "loads")
        else:
            bad("GET /learning-path", f"status={res.status_code}")

        # Explore with search
        res = await client.get("/explore", params={"search": "RAG"})
        if res.status_code == 200 and "course-card" in res.text or "product" in res.text.lower():
            ok("GET /explore?search=RAG", "semantic search UI")
        else:
            bad("GET /explore?search=RAG", f"status={res.status_code}")

    print()
    print("=" * 50)
    print(f"Result: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
