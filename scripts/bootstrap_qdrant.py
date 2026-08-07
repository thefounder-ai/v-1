#!/usr/bin/env python3
"""Create Qdrant collection and index all active catalog products from Supabase."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import admin_list_products, admin_update_vector_status, list_products  # noqa: E402
from app.config import settings  # noqa: E402
from app.vector_sync import upsert_product, VectorSyncError  # noqa: E402


def _access_token() -> str:
    service_role = settings.supabase_service_role_key
    if service_role:
        return service_role
    if settings.supabase_anon_key:
        print("WARN  SUPABASE_SERVICE_ROLE_KEY missing — indexing Qdrant only (status may stay pending).")
        return settings.supabase_anon_key
    raise SystemExit("Supabase is not configured in .env")


async def _sync_one(access_token: str, product: dict, *, update_status: bool) -> bool:
    product_id = product["id"]
    attempts = int(product.get("vector_sync_attempts") or 0) + 1
    try:
        if update_status:
            await admin_update_vector_status(
                access_token,
                product_id,
                status="pending",
                error=None,
                attempts=attempts,
            )
        await upsert_product(product)
        if update_status:
            await admin_update_vector_status(
                access_token,
                product_id,
                status="synced",
                error=None,
                attempts=attempts,
                synced_at=datetime.now(timezone.utc).isoformat(),
            )
        return True
    except (VectorSyncError, Exception) as error:
        if update_status:
            try:
                await admin_update_vector_status(
                    access_token,
                    product_id,
                    status="failed",
                    error=str(error),
                    attempts=attempts,
                )
            except Exception:
                pass
        print(f"  FAIL  {product.get('title', product_id)} — {error}")
        return False


async def main() -> None:
    print("SkillOrbit Qdrant bootstrap")
    print("=" * 50)
    print(f"  Qdrant URL:      {settings.qdrant_url}")
    print(f"  Collection:      {settings.qdrant_collection}")
    print(f"  Mesh configured: {settings.mesh_configured}")
    print(f"  Vector configured: {settings.vector_configured}")
    print()

    if not settings.mesh_configured or not settings.vector_configured:
        raise SystemExit("Set MESH_API_KEY, QDRANT_URL, and QDRANT_API_KEY in .env")

    token = _access_token()
    use_service_role = bool(settings.supabase_service_role_key)

    try:
        products = await admin_list_products(token)
    except Exception:
        products = await list_products()
        use_service_role = False
        print("WARN  Admin list failed — using public active catalog only.")

    active = [p for p in products if p.get("is_active")]
    pending = [p for p in active if p.get("vector_sync_status") != "synced"]
    force_all = "--all" in sys.argv
    to_index = active if force_all else pending

    print(f"  Active products: {len(active)}")
    print(f"  Already synced:  {len(active) - len(pending)}")
    print(f"  To index:        {len(to_index)}")
    print()

    if not to_index:
        print("Nothing to index. Catalog may be empty — run Supabase migrations 002, 010, 013.")
        return

    indexed = 0
    failed = 0
    for i, product in enumerate(to_index, start=1):
        title = (product.get("title") or product["id"])[:50]
        print(f"  [{i}/{len(to_index)}] {title}...")
        if await _sync_one(token, product, update_status=use_service_role):
            indexed += 1
            print("         OK")
        else:
            failed += 1

    print()
    print("=" * 50)
    print(f"Indexed: {indexed}, failed: {failed}")
    if not use_service_role and indexed:
        print("Add SUPABASE_SERVICE_ROLE_KEY to .env to mark rows as synced in Supabase.")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
