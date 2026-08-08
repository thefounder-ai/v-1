#!/usr/bin/env python3
"""Authenticated live E2E against deployed SkillOrbit."""

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

BASE = os.environ.get("LIVE_BASE_URL", "https://v-1-ora9.onrender.com")
EMAIL = os.environ.get("LIVE_TEST_EMAIL", "954954@sendora.me")
PASSWORD = os.environ.get("LIVE_TEST_PASSWORD", "954954@sendora.me")

PASS = 0
FAIL = 0
BUGS: list[str] = []


def ok(label: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    msg = f"{label}" + (f" — {detail}" if detail else "")
    BUGS.append(msg)
    print(f"  FAIL  {msg}")


async def main() -> int:
    print(f"Authenticated live E2E — {BASE}")
    print(f"User: {EMAIL}")
    print("=" * 60)

    async with httpx.AsyncClient(base_url=BASE, timeout=120.0, follow_redirects=False) as client:
        started = time.perf_counter()
        res = await client.post("/auth/login", data={"email": EMAIL, "password": PASSWORD})
        elapsed = time.perf_counter() - started
        loc = res.headers.get("location") or ""
        if res.status_code in {302, 303, 307} and ("/dashboard" in loc or "/onboarding" in loc):
            ok("Login", f"{elapsed:.1f}s -> {loc}")
        else:
            bad("Login", f"status={res.status_code} loc={loc}")
            return 1

        for path in ("/dashboard", "/explore", "/bookmarks", "/recommendations", "/trace", "/demo"):
            started = time.perf_counter()
            res = await client.get(path)
            elapsed = time.perf_counter() - started
            if res.status_code == 200:
                ok(f"GET {path}", f"{elapsed:.1f}s")
            else:
                bad(f"GET {path}", f"status={res.status_code} {elapsed:.1f}s")

        started = time.perf_counter()
        res = await client.get("/explore", params={"search": "production RAG"})
        elapsed = time.perf_counter() - started
        if res.status_code == 200 and "Production RAG" in res.text:
            ok("Semantic search", f"{elapsed:.1f}s")
        else:
            bad("Semantic search", f"status={res.status_code}")

        started = time.perf_counter()
        res = await client.post("/api/recommendations/generate")
        elapsed = time.perf_counter() - started
        if res.status_code == 200:
            data = res.json()
            items = data.get("items") or []
            ok("POST /api/recommendations/generate", f"{elapsed:.1f}s items={len(items)} cached={data.get('cached')}")
            rec_id = data.get("id")
        else:
            bad("POST /api/recommendations/generate", f"status={res.status_code} body={res.text[:200]}")
            rec_id = None

        started = time.perf_counter()
        res = await client.post("/api/interest-profile/refresh")
        elapsed = time.perf_counter() - started
        if res.status_code == 200:
            ok("POST /api/interest-profile/refresh", f"{elapsed:.1f}s")
        else:
            bad("POST /api/interest-profile/refresh", f"status={res.status_code}")

        products = await client.get("/explore", params={"search": "python"})
        product_id = None
        if products.status_code == 200:
            import re

            match = re.search(r"/resource/([0-9a-f-]{36})", products.text)
            product_id = match.group(1) if match else None

        if product_id:
            res = await client.post(
                "/api/events",
                json={
                    "events": [{
                        "event_id": str(uuid4()),
                        "event_type": "bookmark_added",
                        "resource_id": product_id,
                    }]
                },
            )
            if res.status_code == 200:
                ok("POST /api/events bookmark", res.json().get("accepted", ""))
            else:
                bad("POST /api/events bookmark", f"status={res.status_code}")

            res = await client.get("/bookmarks")
            if res.status_code == 200 and product_id in res.text:
                ok("GET /bookmarks shows saved item")
            else:
                bad("GET /bookmarks", "saved resource not visible")

            res = await client.post(
                f"/api/progress/{product_id}",
                data={"progress_status": "started"},
            )
            if res.status_code == 200:
                ok("POST /api/progress started")
            else:
                bad("POST /api/progress", f"status={res.status_code}")
        else:
            bad("Resource discovery", "no product id from explore")

        if rec_id:
            res = await client.post(
                f"/api/recommendations/{rec_id}/feedback",
                json={"feedback": "useful"},
            )
            if res.status_code == 200:
                ok("POST recommendation feedback")
            else:
                bad("POST recommendation feedback", f"status={res.status_code} {res.text[:120]}")

            res = await client.post(f"/api/recommendations/{rec_id}/email")
            if res.status_code == 200:
                ok("POST recommendation email", res.json().get("status", ""))
            elif res.status_code == 503 and "email" in res.text.lower():
                ok("POST recommendation email", "503 expected if Resend not configured")
            else:
                bad("POST recommendation email", f"status={res.status_code} {res.text[:120]}")

        res = await client.get("/admin/products")
        if res.status_code in {302, 303, 307}:
            ok("Admin guard", "redirects non-admin")
        elif res.status_code == 403:
            ok("Admin guard", "forbidden for non-admin")
        elif res.status_code == 200:
            ok("Admin products", "accessible (admin user)")
        else:
            bad("Admin guard", f"status={res.status_code}")

        chunk = b""
        try:
            async with asyncio.timeout(8):
                async with client.stream("GET", "/api/events/stream") as stream:
                    if stream.status_code != 200:
                        bad("GET /api/events/stream", f"status={stream.status_code}")
                    elif "text/event-stream" not in (stream.headers.get("content-type") or ""):
                        bad("GET /api/events/stream", "wrong content-type")
                    else:
                        async for part in stream.aiter_bytes():
                            chunk += part
                            if len(chunk) > 80:
                                break
                        if b"connected" in chunk or b"event:" in chunk:
                            ok("GET /api/events/stream", "SSE connected")
                        else:
                            bad("GET /api/events/stream", f"unexpected chunk: {chunk[:80]!r}")
        except TimeoutError:
            bad("GET /api/events/stream", "timed out before connected event")

    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if BUGS:
        print("\nBugs found:")
        for bug in BUGS:
            print(f"  - {bug}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
