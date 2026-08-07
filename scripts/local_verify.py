#!/usr/bin/env python3
"""Local integration verification — run while uvicorn is on :5000."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.catalog import list_products  # noqa: E402
from app.vector_sync import semantic_product_ids  # noqa: E402

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


async def check_http_pages() -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        health = await client.get(f"{BASE}/health")
        if health.status_code == 200 and health.json().get("status") == "ok":
            body = health.json()
            ok("GET /health", f"supabase={body.get('supabase')}, mesh={body.get('mesh')}, vector={body.get('vector')}")
        else:
            bad("GET /health", str(health.status_code))

        for path in ("/", "/explore", "/login", "/signup"):
            res = await client.get(f"{BASE}{path}")
            if res.status_code == 200 and len(res.text) > 200:
                ok(f"GET {path}", f"{len(res.text)} bytes")
            else:
                bad(f"GET {path}", f"status={res.status_code}")

        res = await client.get(f"{BASE}/dashboard", follow_redirects=False)
        if res.status_code in (303, 307, 302) and "/login" in (res.headers.get("location") or ""):
            ok("GET /dashboard redirects unauthenticated")
        else:
            bad("GET /dashboard auth guard", f"status={res.status_code}")

        res = await client.get(f"{BASE}/bookmarks", follow_redirects=False)
        if res.status_code in (303, 307, 302):
            ok("GET /bookmarks redirects unauthenticated")
        else:
            bad("GET /bookmarks auth guard", f"status={res.status_code}")


async def check_catalog() -> None:
    try:
        products = await list_products()
        if len(products) >= 30:
            ok("Supabase catalog", f"{len(products)} active products")
        else:
            bad("Supabase catalog", f"only {len(products)} products (expected 30+)")
    except Exception as error:
        bad("Supabase catalog", str(error))


async def check_qdrant_mesh() -> None:
    try:
        ids = await semantic_product_ids("production RAG systems", limit=5)
        if ids:
            ok("Qdrant + Mesh semantic search", f"{len(ids)} matches")
        else:
            bad("Qdrant + Mesh semantic search", "no matches — index pending in admin?")
    except Exception as error:
        bad("Qdrant + Mesh semantic search", str(error))


async def check_routes_registered() -> None:
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    required = {
        "/bookmarks",
        "/api/progress/{product_id}",
        "/api/recommendations/generate",
        "/api/events",
        "/admin/sync-health",
    }
    missing = required - paths
    if not missing:
        ok("API routes registered", str(len(required)) + " critical routes")
    else:
        bad("API routes registered", f"missing {missing}")


async def main() -> None:
    print("SkillOrbit local verification")
    print("=" * 50)
    print("Config:")
    print(f"  supabase: {'yes' if settings.supabase_configured else 'NO'}")
    print(f"  mesh:     {'yes' if settings.mesh_configured else 'NO'}")
    print(f"  qdrant:   {'yes' if settings.vector_configured else 'NO'}")
    print(f"  resend:   {'yes' if settings.resend_configured else 'no'}")
    print(f"  service_role: {'yes' if settings.supabase_service_role_key else 'no (digest skipped)'}")
    print()
    print("Checks:")
    await check_routes_registered()
    await check_http_pages()
    await check_catalog()
    await check_qdrant_mesh()
    print()
    print("=" * 50)
    print(f"Result: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
