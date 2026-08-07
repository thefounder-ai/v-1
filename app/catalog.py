from typing import Any

import httpx

from app.config import settings

CATALOG_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class CatalogError(RuntimeError):
    """Raised when the catalog cannot be read from Supabase."""


def _headers(access_token: str | None = None) -> dict[str, str]:
    if not settings.supabase_configured:
        raise CatalogError("Supabase is not configured.")
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token or settings.supabase_anon_key}",
    }
    if access_token:
        headers["Prefer"] = "return=representation"
    return headers


CAREER_GOALS = [
    "AI Engineer",
    "Backend Developer",
    "Generative AI Builder",
    "Data/ML Foundations",
    "Production AI Product Builder",
]


async def list_products_for_goal(career_goal: str) -> list[dict[str, Any]]:
    safe_goal = career_goal.strip()
    if not safe_goal:
        return []
    params: list[tuple[str, str]] = [
        ("select", "*"),
        ("is_active", "eq.true"),
        ("career_goals", f"cs.[\"{safe_goal}\"]"),
        ("order", "difficulty.asc,created_at.asc"),
        ("limit", "12"),
    ]
    return await _request("GET", "/rest/v1/products", params=params)


async def list_products(
    *,
    search: str = "",
    category: str = "",
    difficulty: str = "",
    content_type: str = "",
    career_goal: str = "",
) -> list[dict[str, Any]]:
    params: list[tuple[str, str]] = [
        ("select", "*"),
        ("is_active", "eq.true"),
        ("order", "created_at.desc"),
        ("limit", "100"),
    ]
    if search:
        safe_search = search.replace(",", " ").replace("*", " ")
        params.append(("or", f"(title.ilike.*{safe_search}*,description.ilike.*{safe_search}*,category.ilike.*{safe_search}*)"))
    if category:
        params.append(("category", f"eq.{category}"))
    if difficulty:
        params.append(("difficulty", f"eq.{difficulty}"))
    if content_type:
        params.append(("content_type", f"eq.{content_type}"))
    if career_goal:
        params.append(("career_goals", f"cs.[\"{career_goal.strip()}\"]"))
    return await _request("GET", "/rest/v1/products", params=params)


async def sync_health_summary(access_token: str) -> dict[str, int]:
    products = await admin_list_products(access_token)
    summary = {
        "total": len(products),
        "active": 0,
        "synced": 0,
        "pending": 0,
        "failed": 0,
        "inactive": 0,
    }
    for product in products:
        if product.get("is_active"):
            summary["active"] += 1
            status = product.get("vector_sync_status") or "pending"
            if status == "synced":
                summary["synced"] += 1
            elif status == "failed":
                summary["failed"] += 1
            else:
                summary["pending"] += 1
        else:
            summary["inactive"] += 1
    return summary


async def get_product(product_id: str) -> dict[str, Any] | None:
    rows = await _request(
        "GET",
        "/rest/v1/products",
        params=[
            ("select", "*"),
            ("id", f"eq.{product_id}"),
            ("is_active", "eq.true"),
            ("limit", "1"),
        ],
    )
    return rows[0] if rows else None


async def list_products_by_ids(
    product_ids: list[str],
    *,
    category: str = "",
    difficulty: str = "",
) -> list[dict[str, Any]]:
    if not product_ids:
        return []
    params: list[tuple[str, str]] = [
        ("select", "*"),
        ("is_active", "eq.true"),
        ("id", f"in.({','.join(product_ids)})"),
    ]
    if category:
        params.append(("category", f"eq.{category}"))
    if difficulty:
        params.append(("difficulty", f"eq.{difficulty}"))
    rows = await _request("GET", "/rest/v1/products", params=params)
    order = {product_id: index for index, product_id in enumerate(product_ids)}
    return sorted(rows, key=lambda product: order.get(product["id"], len(order)))


async def admin_get_product(access_token: str, product_id: str) -> dict[str, Any] | None:
    rows = await _request(
        "GET",
        "/rest/v1/products",
        params=[
            ("select", "*"),
            ("id", f"eq.{product_id}"),
            ("limit", "1"),
        ],
        access_token=access_token,
    )
    return rows[0] if rows else None


async def admin_list_products(access_token: str) -> list[dict[str, Any]]:
    return await _request(
        "GET",
        "/rest/v1/products",
        params=[("select", "*"), ("order", "created_at.desc"), ("limit", "200")],
        access_token=access_token,
    )


async def admin_create_product(access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = await _request(
        "POST",
        "/rest/v1/products",
        params=[],
        json=payload,
        access_token=access_token,
    )
    if not rows:
        raise CatalogError("The resource could not be created.")
    return rows[0]


async def admin_update_product(
    access_token: str,
    product_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    rows = await _request(
        "PATCH",
        "/rest/v1/products",
        params=[("id", f"eq.{product_id}")],
        json=payload,
        access_token=access_token,
    )
    if not rows:
        raise CatalogError("The resource could not be updated.")
    return rows[0]


async def admin_update_vector_status(
    access_token: str,
    product_id: str,
    *,
    status: str,
    error: str | None = None,
    attempts: int | None = None,
    synced_at: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "vector_sync_status": status,
        "vector_sync_error": error,
    }
    if attempts is not None:
        payload["vector_sync_attempts"] = attempts
    if synced_at is not None:
        payload["vector_synced_at"] = synced_at
    return await admin_update_product(access_token, product_id, payload)


async def _request(
    method: str,
    path: str,
    *,
    params: list[tuple[str, str]],
    json: dict[str, Any] | None = None,
    access_token: str | None = None,
) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=CATALOG_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{settings.supabase_url}{path}",
                headers=_headers(access_token),
                params=params,
                json=json,
            )
    except (httpx.HTTPError, CatalogError) as error:
        raise CatalogError("The catalog service is temporarily unavailable.") from error
    if response.is_error:
        raise CatalogError("The catalog is not ready. Run the catalog migration in Supabase.")
    body = response.json()
    return body if isinstance(body, list) else []