#!/usr/bin/env python3
"""AI pipeline verification without auth — Mesh + Qdrant + catalog grounding."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import list_products, list_products_by_ids  # noqa: E402
from app.interest import build_interest_profile  # noqa: E402
from app.recommendations import _mesh_narrative, build_retrieval_query  # noqa: E402
from app.vector_sync import semantic_product_matches  # noqa: E402

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
    print("SkillOrbit AI pipeline verification")
    print("=" * 50)

    products = await list_products()
    if len(products) >= 30:
        ok("Catalog", f"{len(products)} products")
    else:
        bad("Catalog", f"only {len(products)}")

    matches = await semantic_product_matches("production RAG systems embeddings", limit=6)
    if matches:
        ok("Semantic retrieval", f"{len(matches)} matches, top score={matches[0].get('score', '?')}")
    else:
        bad("Semantic retrieval", "no matches")

    learner = {
        "career_goal": "Generative AI Builder",
        "current_level": "Intermediate",
        "weekly_minutes": 300,
    }
    sample_product = products[0]
    events = [
        {
            "event_type": "catalog_search",
            "search_query": "production RAG",
            "resource_id": None,
        },
        {
            "event_type": "resource_dwell",
            "resource_id": sample_product["id"],
            "duration_seconds": 60,
        },
        {
            "event_type": "bookmark_added",
            "resource_id": sample_product["id"],
        },
    ]
    profile = build_interest_profile(events, {sample_product["id"]: sample_product}, learner)
    if profile.get("meaningful_event_count", 0) >= 2:
        ok("Interest profile", f"{profile.get('meaningful_event_count')} meaningful events")
    else:
        bad("Interest profile", str(profile))

    query = build_retrieval_query(profile, learner)
    if "Generative AI" in query or "RAG" in query.upper():
        ok("Retrieval query", query.split("\n")[0][:60])
    else:
        bad("Retrieval query", "missing learner signals")

    ids = [match["product_id"] for match in matches]
    candidates = await list_products_by_ids(ids)
    if len(candidates) >= 3:
        ok("Grounded candidates", f"{len(candidates)} catalog items")
    else:
        bad("Grounded candidates", f"only {len(candidates)}")

    print("  ... calling Mesh for narrative (up to 60s)")
    narrative = await _mesh_narrative(query, profile, candidates)
    summary = (narrative.get("summary") or "").strip()
    next_step = (narrative.get("next_step") or "").strip()
    reasons = narrative.get("item_reasons") or []
    if summary and next_step and reasons:
        ok("Mesh narrative", f"summary={len(summary)} chars, {len(reasons)} reasons")
    else:
        bad("Mesh narrative", str(narrative)[:200])

    valid_ids = {product["id"] for product in candidates}
    grounded = all(reason.get("product_id") in valid_ids for reason in reasons)
    if grounded:
        ok("Grounding check", "all reasons reference real product IDs")
    else:
        bad("Grounding check", "hallucinated product IDs in reasons")

    print()
    print("=" * 50)
    print(f"Result: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
