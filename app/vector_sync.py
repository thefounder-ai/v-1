from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

import httpx
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import ResponseHandlingException

from app.config import settings


class VectorSyncError(RuntimeError):
    """Raised when Mesh embedding or Qdrant indexing fails."""


T = TypeVar("T")
_TRANSIENT_QDRANT_ERRORS = (httpx.ConnectError, httpx.ReadTimeout, OSError)


def _is_transient_qdrant_error(error: Exception) -> bool:
    if isinstance(error, _TRANSIENT_QDRANT_ERRORS):
        return True
    if isinstance(error, ResponseHandlingException):
        cause = error.__cause__ or error
        if isinstance(cause, _TRANSIENT_QDRANT_ERRORS):
            return True
    message = str(error).lower()
    return "getaddrinfo failed" in message or "temporary failure in name resolution" in message


async def _with_qdrant_retries(operation: Callable[[], Awaitable[T]], *, attempts: int = 3) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as error:
            if not _is_transient_qdrant_error(error):
                raise
            last_error = error
            if attempt + 1 < attempts:
                await asyncio.sleep(0.4 * (attempt + 1))
    if last_error:
        raise last_error
    raise VectorSyncError("Qdrant request failed.")


def _require_configured() -> None:
    missing = []
    if not settings.mesh_configured:
        missing.append("MESH_API_KEY")
    if not settings.vector_configured:
        missing.extend(["QDRANT_URL", "QDRANT_API_KEY"])
    if missing:
        raise VectorSyncError(f"Vector indexing is not configured: {', '.join(missing)}.")


def product_embedding_text(product: dict[str, Any]) -> str:
    fields = [
        product.get("title", ""),
        product.get("short_summary", ""),
        product.get("description", ""),
        f"Category: {product.get('category', '')}",
        f"Difficulty: {product.get('difficulty', '')}",
        f"Skills: {', '.join(product.get('skills') or [])}",
        f"Prerequisites: {', '.join(product.get('prerequisites') or [])}",
        f"Learning outcomes: {', '.join(product.get('learning_outcomes') or [])}",
        f"Career goals: {', '.join(product.get('career_goals') or [])}",
    ]
    return "\n".join(str(field).strip() for field in fields if str(field).strip())


async def create_embedding(text: str) -> list[float]:
    _require_configured()
    client = AsyncOpenAI(
        base_url=settings.mesh_api_base_url,
        api_key=settings.mesh_api_key,
        timeout=30.0,
    )
    try:
        response = await client.embeddings.create(
            model=settings.mesh_embedding_model,
            input=text,
        )
        if not response.data or not response.data[0].embedding:
            raise VectorSyncError("Mesh returned an empty embedding.")
        return response.data[0].embedding
    except Exception as error:
        if isinstance(error, VectorSyncError):
            raise
        raise VectorSyncError("Mesh embedding request failed.") from error
    finally:
        await client.close()


def _qdrant_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=30,
        check_compatibility=False,
    )


async def ensure_collection(vector_size: int) -> None:
    _require_configured()

    async def _ensure() -> None:
        client = _qdrant_client()
        try:
            collections = await client.get_collections()
            names = {collection.name for collection in collections.collections}
            if settings.qdrant_collection not in names:
                await client.create_collection(
                    collection_name=settings.qdrant_collection,
                    vectors_config=qdrant_models.VectorParams(
                        size=vector_size,
                        distance=qdrant_models.Distance.COSINE,
                    ),
                )
            try:
                await client.create_payload_index(
                    collection_name=settings.qdrant_collection,
                    field_name="is_active",
                    field_schema=qdrant_models.PayloadSchemaType.BOOL,
                    wait=True,
                )
            except Exception:
                # Index may already exist when multiple requests bootstrap the collection.
                pass
        finally:
            await client.close()

    try:
        await _with_qdrant_retries(_ensure)
    except Exception as error:
        raise VectorSyncError("Qdrant collection setup failed.") from error


def _payload(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": product["id"],
        "slug": product.get("slug"),
        "title": product.get("title"),
        "category": product.get("category"),
        "difficulty": product.get("difficulty"),
        "content_type": product.get("content_type"),
        "skills": product.get("skills") or [],
        "career_goals": product.get("career_goals") or [],
        "is_active": product.get("is_active", True),
    }


async def upsert_product(product: dict[str, Any]) -> None:
    vector = await create_embedding(product_embedding_text(product))
    await ensure_collection(len(vector))
    client = _qdrant_client()
    try:
        await client.upsert(
            collection_name=settings.qdrant_collection,
            points=[
                qdrant_models.PointStruct(
                    id=product["id"],
                    vector=vector,
                    payload=_payload(product),
                )
            ],
            wait=True,
        )
    except Exception as error:
        raise VectorSyncError("Qdrant product upsert failed.") from error
    finally:
        await client.close()


async def delete_product_vector(product_id: str) -> None:
    _require_configured()
    client = _qdrant_client()
    try:
        await client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=qdrant_models.PointIdsList(points=[product_id]),
            wait=True,
        )
    except Exception as error:
        raise VectorSyncError("Qdrant product removal failed.") from error
    finally:
        await client.close()


async def semantic_product_ids(query: str, limit: int = 12) -> list[str]:
    matches = await semantic_product_matches(query, limit=limit)
    return [match["product_id"] for match in matches]


async def semantic_product_matches(query: str, limit: int = 12) -> list[dict[str, Any]]:
    vector = await create_embedding(query)
    await ensure_collection(len(vector))

    async def _search() -> list[dict[str, Any]]:
        client = _qdrant_client()
        try:
            response = await client.query_points(
                collection_name=settings.qdrant_collection,
                query=vector,
                query_filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="is_active",
                            match=qdrant_models.MatchValue(value=True),
                        )
                    ]
                ),
                limit=limit,
                with_payload=True,
            )
            matches: list[dict[str, Any]] = []
            for point in response.points:
                product_id = (point.payload or {}).get("product_id")
                if product_id:
                    matches.append({
                        "product_id": str(product_id),
                        "score": round(float(point.score or 0), 5),
                        "payload": point.payload or {},
                    })
            return matches
        finally:
            await client.close()

    try:
        return await _with_qdrant_retries(_search)
    except Exception as error:
        raise VectorSyncError("Qdrant semantic search failed.") from error