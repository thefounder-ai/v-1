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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.demo_service import DEMO_STEPS, apply_demo_seed  # noqa: E402

DEMO_STEPS_TEXT = """
SkillOrbit — 60 second judge demo
=================================
1. Open /demo for guided judge mode (or /explore → search "production RAG")
2. Sign up → onboarding → pick "AI Engineer"
3. Admin → Seed demo activity (or python scripts/demo_seed.py --apply)
4. Dashboard → interest radar + live activity feed
5. Generate path → Share path + /trace for Mesh observability
6. Refresh path → show "What changed" diff
7. Admin → add resource → Index pending → appears in search

To pre-seed activity for a demo account:
  set DEMO_USER_EMAIL=demo@example.com
  python scripts/demo_seed.py --apply

GitHub secrets required: MESH_API_KEY, SUBMISSION_TOKEN
Migrations: 001 through 016 in Supabase SQL editor.
After seeding catalog: python scripts/bootstrap_qdrant.py
""".strip()


async def main_async(apply: bool) -> None:
    if apply:
        email = os.environ.get("DEMO_USER_EMAIL", "").strip()
        if not email:
            raise SystemExit("Set DEMO_USER_EMAIL to the demo learner account email.")
        result = await apply_demo_seed(email=email)
        print(
            f"Seeded {result['events_seeded']} activity events for "
            f"{email or result['user_id']} ({result['user_id']})."
        )
        print("Next: sign in → Dashboard → Generate path → Share /path/{id}")
    else:
        print(DEMO_STEPS_TEXT)
        print(f"\nGuided steps in app: {len(DEMO_STEPS)} steps at /demo")


def main() -> None:
    parser = argparse.ArgumentParser(description="SkillOrbit demo helper")
    parser.add_argument("--apply", action="store_true", help="Insert demo activity events")
    args = parser.parse_args()
    asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    main()
