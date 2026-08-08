#!/usr/bin/env python3
"""SmartReco 2026 final submission verification.

Runs repository checks, unit tests, static judge-feature audit, and optional live smoke.

Usage:
  python scripts/competition_verify.py           # full verify (tests + static + live)
  python scripts/competition_verify.py --ci      # CI mode: tests + static only
  python scripts/competition_verify.py --static  # static judge checklist only
  python scripts/competition_verify.py --live-only

Authenticated E2E (separate, server must be running on :5000):
  python scripts/local_e2e.py
"""

from __future__ import annotations

import argparse
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
    print(f"  PASS  {label}" + (f" - {detail}" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  FAIL  {label}" + (f" - {detail}" if detail else ""))


def warn(label: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  WARN  {label}" + (f" - {detail}" if detail else ""))


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
        ok("Unit tests", f"{count} passed")
    else:
        tail = (result.stdout + result.stderr)[-800:]
        bad("Unit tests", tail)


def check_migrations() -> None:
    if len(MIGRATIONS) >= 16:
        ok("Migrations on disk", f"{len(MIGRATIONS)} files (001-016)")
    else:
        bad("Migrations on disk", f"only {len(MIGRATIONS)} files")
    for name in (
        "015_email_delivery_kind.sql",
        "016_recommendation_change_explanation.sql",
    ):
        if any(m.name == name for m in MIGRATIONS):
            ok(f"Migration file {name}", "present")
        else:
            bad(f"Migration file {name}", "missing")


def check_submission_routes() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    required = {
        "/health",
        "/explore",
        "/demo",
        "/dashboard",
        "/trace",
        "/path/{recommendation_id}",
        "/path/{recommendation_id}/print",
        "/api/events",
        "/api/events/stream",
        "/api/recommendations/generate",
        "/api/recommendations/{recommendation_id}/feedback",
        "/api/recommendations/{recommendation_id}/email",
        "/api/admin/demo-seed",
        "/api/cron/weekly-digest",
        "/admin/products",
        "/admin/sync-health",
    }
    missing = required - paths
    if not missing:
        ok("Submission routes", f"{len(required)} critical routes registered")
    else:
        bad("Submission routes", f"missing {sorted(missing)}")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def check_judge_checklist_static() -> None:
    """Code-level verification of Tier S / judge proof points."""
    interest = _read("app/interest.py")
    triggers = _read("app/triggers.py")
    if "refresh_recommended" in interest and "should_auto_generate" in triggers:
        ok("Judge: behavior-driven refresh", "interest profile + trigger policy")
    else:
        bad("Judge: behavior-driven refresh", "missing refresh hooks")

    trace_html = _read("app/templates/trace.html")
    rec_py = _read("app/recommendations.py")
    if "retrieval_metadata" in trace_html and "candidates" in trace_html:
        ok("Judge: retrieval scores visible", "/trace candidate table")
    else:
        bad("Judge: retrieval scores visible", "trace template incomplete")
    if "top_score" in rec_py and "annotate_retrieval_candidates" in rec_py:
        ok("Judge: retrieval metadata stored", "scores in recommendations.py")
    else:
        bad("Judge: retrieval metadata stored", "missing score fields")

    if "change_explanation" in rec_py and any(
        m.name == "016_recommendation_change_explanation.sql" for m in MIGRATIONS
    ):
        ok("Judge: why-it-changed", "change_explanation + migration 016")
    else:
        bad("Judge: why-it-changed", "missing explanation pipeline")

    path_health = _read("app/path_health.py")
    if "generic_baseline_path" in path_health and "build_path_intelligence" in path_health:
        ok("Judge: counterfactual comparison", "path_health.py")
    else:
        bad("Judge: counterfactual comparison", "path intelligence missing")

    langgraph = _read("app/langgraph_agent.py")
    if "build_recommendation_graph" in langgraph and "/trace" in {getattr(r, "path", "") for r in app.routes}:
        ok("Judge: LangGraph trace", "graph + /trace page")
    else:
        bad("Judge: LangGraph trace", "missing graph or trace route")

    main_py = _read("app/main.py")
    vector = _read("app/vector_sync.py")
    if "upsert_product" in vector and "/admin/products" in main_py:
        ok("Judge: admin dual-write", "Supabase CRUD + Qdrant upsert")
    else:
        bad("Judge: admin dual-write", "admin vector sync missing")

    digest = _read("app/digest.py")
    email = _read("app/email_delivery.py")
    if "weekly_digest" in digest and "/api/recommendations/{recommendation_id}/email" in {
        getattr(r, "path", "") for r in app.routes
    }:
        ok("Judge: weekly digest + manual email", "digest scheduler + Resend route")
    else:
        bad("Judge: weekly digest + manual email", "missing digest or email")

    for path in ("app/recommendations.py", "app/vector_sync.py"):
        source = _read(path)
        if "mesh_api_base_url" in source and "mesh_api_key" in source:
            ok(f"Judge: Mesh gateway in {path}", "AsyncOpenAI uses Mesh base URL")
        else:
            bad(f"Judge: Mesh gateway in {path}", "direct API risk")
    if "api.openai.com" in rec_py or "api.openai.com" in vector:
        bad("Judge: Mesh API only", "found api.openai.com reference")
    else:
        ok("Judge: Mesh API only", "no direct OpenAI endpoint in app code")


def check_github_workflows() -> None:
    smartreco = ROOT / ".github" / "workflows" / "smartreco-checks.yml"
    quality = ROOT / ".github" / "workflows" / "quality.yml"
    if smartreco.is_file():
        ok("Workflow: SmartReco Checks", smartreco.name)
    else:
        bad("Workflow: SmartReco Checks", "missing")
    if quality.is_file():
        body = quality.read_text(encoding="utf-8")
        if "competition_verify" in body:
            ok("Workflow: Quality", "runs competition_verify on push")
        elif "unittest" in body:
            ok("Workflow: Quality", "runs unit tests on push")
        else:
            warn("Workflow: Quality", "unittest step not found")
    else:
        bad("Workflow: Quality", "missing")


def check_readme_submission_block() -> None:
    readme = _read("README.md")
    for needle in ("v-1-ora9.onrender.com", "/demo", "DEMO_RUNBOOK"):
        if needle in readme:
            ok(f"README mentions {needle}", "submission block")
        else:
            bad(f"README mentions {needle}", "missing from README top")


async def check_live_deployment(base_url: str) -> None:
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        health = await client.get(f"{base_url}/health")
        if health.status_code == 200 and health.json().get("status") == "ok":
            body = health.json()
            ok("Live /health", f"mesh={body.get('mesh')}, vector={body.get('vector')}")
        else:
            bad("Live /health", f"HTTP {health.status_code}")

        for path in ("/", "/explore", "/login", "/demo"):
            res = await client.get(f"{base_url}{path}")
            if res.status_code == 200 and len(res.text) > 200:
                ok(f"Live GET {path}", f"{len(res.text)} bytes")
            elif path == "/demo" and res.status_code == 404:
                warn(f"Live GET {path}", "404 - deploy latest main to Render (Phase 6+)")
            else:
                bad(f"Live GET {path}", f"HTTP {res.status_code}")

        search = await client.get(f"{base_url}/explore", params={"search": "production RAG"})
        if search.status_code == 200 and (
            "resource-card" in search.text or "marketplace-card" in search.text
        ):
            ok("Live semantic search", "catalog cards rendered")
        else:
            bad("Live semantic search", f"HTTP {search.status_code}")

        share = await client.get(f"{base_url}/path/00000000-0000-0000-0000-000000000000")
        if share.status_code in (200, 404):
            ok("Live share route", f"HTTP {share.status_code} (public, no auth redirect)")
        else:
            bad("Live share route", f"HTTP {share.status_code}")

        cron = await client.get(f"{base_url}/api/cron/weekly-digest")
        if cron.status_code == 401:
            ok("Live cron guard", "401 without secret")
        elif cron.status_code == 200:
            warn("Live cron guard", "200 without secret - set CRON_SECRET on Render")
        else:
            bad("Live cron guard", f"HTTP {cron.status_code}")

        ui = await client.get(f"{base_url}/static/ui.js")
        if ui.status_code == 200 and "SkillOrbitUI" in ui.text:
            if "TOAST" in ui.text:
                ok("Live static assets", "ui.js with toast catalog")
            else:
                warn("Live static assets", "ui.js live but pre-Phase-7 deploy - push latest")
        else:
            bad("Live static assets", f"HTTP {ui.status_code}")


def print_footer(ci_mode: bool) -> None:
    print()
    print("=" * 60)
    print(f"Result: {PASS} passed, {FAIL} failed, {WARN} warnings")
    print()
    if FAIL:
        print("Submission verify: FAILED - fix items above before deploy.")
        sys.exit(1)
    print("Submission verify: PASSED")
    if WARN:
        print("Review warnings (usually: push latest code + Render manual deploy).")
    print()
    print("Next steps:")
    print("  1. Apply migrations 001-016 in Supabase if not already done")
    print("  2. python scripts/local_e2e.py  (uvicorn on :5000, optional Mesh call)")
    print("  3. Record 2-3 min demo video - script in DEMO_RUNBOOK.md")
    print("  4. Push main -> confirm SmartReco Checks green -> Render manual deploy")
    if ci_mode:
        print("  (CI mode: live checks skipped)")


async def main_async(args: argparse.Namespace) -> None:
    print("SkillOrbit - Competition Submission Verify (Phase 8)")
    print("=" * 60)
    print(f"Live target: {args.base_url}")
    print()

    if not args.live_only:
        print("[1] Repository & migrations")
        check_migrations()
        check_submission_routes()
        check_github_workflows()
        check_readme_submission_block()
        print()
        print("[2] Judge checklist (static code audit)")
        check_judge_checklist_static()
        print()

        if args.static:
            print_footer(ci_mode=True)
            return

        print("[3] Unit tests")
        run_unit_tests()
        print()

    if args.ci:
        print_footer(ci_mode=True)
        return

    if args.live_only:
        print("[Live deployment smoke]")
    else:
        print("[4] Live deployment smoke")
    await check_live_deployment(args.base_url.rstrip("/"))
    print()
    print_footer(ci_mode=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartReco final submission verification")
    parser.add_argument("--ci", action="store_true", help="CI mode: tests + static, skip live HTTP")
    parser.add_argument("--static", action="store_true", help="Static judge checklist only")
    parser.add_argument("--live-only", action="store_true", help="Only run live deployment checks")
    parser.add_argument("--base-url", default=LIVE_BASE, help="Live app URL for smoke tests")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
