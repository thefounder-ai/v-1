#!/usr/bin/env python3
"""Phase 0 baseline verification — run before starting the competition roadmap."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "skillorbit.env")

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

LIVE_BASE = (settings.app_public_url or "https://v-1-ora9.onrender.com").rstrip("/")
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))

PASS = 0
FAIL = 0
WARN = 0


def ok(label: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def warn(label: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  WARN  {label}" + (f" — {detail}" if detail else ""))


def run_unit_tests() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        import re
        match = re.search(r"Ran (\d+) tests", result.stdout)
        count = match.group(1) if match else "all"
        ok("Unit tests", f"{count} tests passed")
    else:
        bad("Unit tests", result.stdout[-500:] or result.stderr[-500:])


def check_migrations_on_disk() -> None:
    if len(MIGRATIONS) >= 15:
        ok("Migration files on disk", f"{len(MIGRATIONS)} files (001–015 present)")
    else:
        bad("Migration files on disk", f"only {len(MIGRATIONS)} found")
    if any(m.name == "015_email_delivery_kind.sql" for m in MIGRATIONS):
        ok("Migration 015 (delivery_kind)", "file present — confirm applied in Supabase SQL editor")
    else:
        bad("Migration 015 (delivery_kind)", "missing file")


def check_local_config() -> None:
    checks = {
        "supabase": settings.supabase_configured,
        "mesh": settings.mesh_configured,
        "qdrant": settings.vector_configured,
        "resend": settings.resend_configured,
        "digest": settings.digest_configured,
        "cron_secret": settings.cron_configured,
    }
    for name, ready in checks.items():
        if ready:
            ok(f"Local env: {name}", "configured")
        elif name in {"resend", "cron_secret"}:
            warn(f"Local env: {name}", "not set locally (OK if only on Render)")
        else:
            bad(f"Local env: {name}", "missing")


def check_routes() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    required = {
        "/health",
        "/explore",
        "/api/events",
        "/api/recommendations/generate",
        "/api/cron/weekly-digest",
        "/api/recommendations/{recommendation_id}/email",
        "/trace",
        "/bookmarks",
        "/admin/sync-health",
    }
    missing = required - paths
    if not missing:
        ok("Critical routes registered", f"{len(required)} routes")
    else:
        bad("Critical routes registered", f"missing {sorted(missing)}")


async def check_live_site() -> None:
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        health = await client.get(f"{LIVE_BASE}/health")
        if health.status_code == 200:
            body = health.json()
            if body.get("status") == "ok":
                ok("Live /health", f"digest={body.get('digest')}, mesh={body.get('mesh')}")
            else:
                bad("Live /health", str(body))
        else:
            bad("Live /health", f"HTTP {health.status_code}")

        for path in ("/", "/explore", "/login"):
            res = await client.get(f"{LIVE_BASE}{path}")
            if res.status_code == 200 and len(res.text) > 200:
                ok(f"Live GET {path}", f"{len(res.text)} bytes")
            else:
                bad(f"Live GET {path}", f"HTTP {res.status_code}")

        search = await client.get(f"{LIVE_BASE}/explore", params={"search": "production RAG"})
        if search.status_code == 200 and "resource-card" in search.text:
            ok("Live semantic search UI", "resource cards rendered")
        else:
            bad("Live semantic search UI", f"HTTP {search.status_code}")

        cron = await client.get(f"{LIVE_BASE}/api/cron/weekly-digest")
        if cron.status_code == 401:
            ok("Live cron auth guard", "401 without secret (expected)")
        elif cron.status_code == 200:
            warn("Live cron auth guard", "200 without secret — CRON_SECRET may be unset on server")
        else:
            bad("Live cron auth guard", f"HTTP {cron.status_code}")

        ui = await client.get(f"{LIVE_BASE}/static/ui.js")
        if ui.status_code == 200 and "SkillOrbitUI" in ui.text:
            ok("Live static ui.js", "SkillOrbitUI loaded")
        else:
            bad("Live static ui.js", f"HTTP {ui.status_code}")


async def main() -> None:
    print("SkillOrbit Phase 0 — Baseline Verification")
    print("=" * 55)
    print(f"Live target: {LIVE_BASE}")
    print(f"Baseline commit (local): run `git log -1 --oneline`")
    print()
    print("[1] Repository")
    check_migrations_on_disk()
    check_routes()
    print()
    print("[2] Local configuration")
    check_local_config()
    print()
    print("[3] Unit tests")
    run_unit_tests()
    print()
    print("[4] Live deployment")
    await check_live_site()
    print()
    print("=" * 55)
    print(f"Result: {PASS} passed, {FAIL} failed, {WARN} warnings")
    print()
    if FAIL:
        print("Phase 0: NOT COMPLETE — fix failures before Phase 1.")
        sys.exit(1)
    print("Phase 0: COMPLETE — safe to start Phase 1.")
    if WARN:
        print("Review warnings (usually Render-only secrets).")


if __name__ == "__main__":
    asyncio.run(main())
