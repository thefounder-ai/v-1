#!/usr/bin/env python3
"""Print SQL migration paths for catalog seeding. Run each file in Supabase SQL editor."""

from pathlib import Path

MIGRATIONS = [
    "supabase/migrations/002_catalog.sql",
    "supabase/migrations/010_catalog_expansion.sql",
    "supabase/migrations/013_catalog_to_80.sql",
]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    print("SkillOrbit catalog seed — run these migrations in order:\n")
    for relative in MIGRATIONS:
        path = root / relative
        status = "found" if path.exists() else "MISSING"
        print(f"  [{status}] {relative}")
    print("\nAfter seeding, index vectors from /admin/products → Index pending resources.")


if __name__ == "__main__":
    main()
