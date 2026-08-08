#!/usr/bin/env python3
"""Public live smoke checks against the deployed SkillOrbit URL."""

from __future__ import annotations

import sys
import time

import httpx

BASE = "https://v-1-ora9.onrender.com"
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


def main() -> int:
    print(f"SkillOrbit live smoke — {BASE}")
    print("=" * 60)

    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        started = time.perf_counter()
        res = client.get(f"{BASE}/health")
        elapsed = time.perf_counter() - started
        if res.status_code == 200 and res.json().get("status") == "ok":
            ok("GET /health", f"{elapsed:.1f}s configured={res.json().get('vector')}")
        else:
            bad("GET /health", f"status={res.status_code}")

        for path in ("/", "/explore", "/login", "/signup", "/demo"):
            started = time.perf_counter()
            res = client.get(f"{BASE}{path}")
            elapsed = time.perf_counter() - started
            if res.status_code == 200:
                ok(f"GET {path}", f"{elapsed:.1f}s")
            else:
                bad(f"GET {path}", f"status={res.status_code} {elapsed:.1f}s")

        started = time.perf_counter()
        res = client.get(f"{BASE}/explore", params={"search": "production RAG"})
        elapsed = time.perf_counter() - started
        if res.status_code == 200 and "Production RAG" in res.text:
            ok("Explore semantic search", f"{elapsed:.1f}s results visible")
        elif res.status_code == 200:
            bad("Explore semantic search", f"{elapsed:.1f}s page loaded but expected result missing")
        else:
            bad("Explore semantic search", f"status={res.status_code}")

        started = time.perf_counter()
        res = client.get(f"{BASE}/dashboard", follow_redirects=False)
        elapsed = time.perf_counter() - started
        if res.status_code in {302, 303, 307} and "/login" in (res.headers.get("location") or ""):
            ok("Dashboard auth guard", f"{elapsed:.1f}s")
        else:
            bad("Dashboard auth guard", f"status={res.status_code}")

        res = client.get(f"{BASE}/static/tracking.js")
        if res.status_code == 200 and "window.skillOrbitFlush" in res.text:
            ok("Tracking bundle", "flush export present")
        else:
            bad("Tracking bundle", "missing skillOrbitFlush export")

        for template_hint, path in (
            ("DOMContentLoaded", "/explore"),
            ("DOMContentLoaded", "/login"),
        ):
            res = client.get(f"{BASE}{path}")
            if template_hint in res.text:
                ok(f"{path} bootstrapping", template_hint)
            else:
                bad(f"{path} bootstrapping", f"missing {template_hint}")

    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
