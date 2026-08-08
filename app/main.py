from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
import asyncio
import json
import logging
from typing import Any
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.activity import (
    ActivityBatch,
    ActivityError,
    batch_has_meaningful_events,
    events_since,
    format_live_event,
    recent_events,
    store_events,
)
from app.live_signals import signal_bus
from app.bookmarks import BookmarkError, bookmarked_product_ids
from app.progress import ProgressError, list_progress, progress_summary, set_progress, learning_streak, weekly_learning_minutes
from app.auth import (
    SupabaseNotConfiguredError,
    clear_session_cookies,
    current_user,
    current_user_context,
    get_profile,
    get_user,
    post_auth_destination,
    require_access_token,
    resolve_access_token,
    set_session_cookies,
    sign_in,
    sign_up,
    upsert_profile,
)
from app.interest import (
    InterestProfileError,
    apply_recommendation_feedback,
    get_interest_profile,
    refresh_interest_profile,
)
from app.path_health import build_path_intelligence, recommendation_api_with_intelligence
from app.demo_service import DEMO_STEPS, apply_demo_seed
from app.recommendations import (
    RecommendationError,
    generate_recommendation,
    get_recommendation_for_share,
    latest_recommendation,
    recommendation_api_payload,
    recommendation_history,
    recommendation_item_categories,
    update_recommendation_status,
)
from app.observability import configure_logging, log_event, new_request_id, request_id_context
from app.email_delivery import EmailDeliveryError, send_recommendation_email
from app.catalog import (
    CAREER_GOALS,
    CatalogError,
    admin_create_product,
    admin_get_product,
    admin_list_products,
    admin_update_product,
    admin_update_vector_status,
    get_product,
    list_products_by_ids,
    list_products,
    list_products_for_goal,
    sync_health_summary,
)
from app.config import settings
from app.vector_sync import (
    VectorSyncError,
    delete_product_vector,
    semantic_product_ids,
    upsert_product,
)
from app.triggers import should_auto_generate, within_cooldown, is_recommendation_fresh
from app.scheduler import shutdown_scheduler, start_scheduler
from app.digest import DigestError, run_weekly_digest

load_dotenv()
configure_logging()
logger = logging.getLogger("skillorbit.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    start_scheduler()
    yield
    shutdown_scheduler()


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _interest_snapshot_labels(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value[:4] if item]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


templates.env.filters["interest_labels"] = _interest_snapshot_labels

app = FastAPI(
    title="SkillOrbit",
    description="An AI career learning navigator powered by behavioral signals.",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.exception_handler(Exception)
async def unhandled_server_error(request: Request, exc: Exception) -> JSONResponse | HTMLResponse:
    if isinstance(exc, HTTPException):
        raise exc
    log_event(
        logger,
        logging.ERROR,
        "unhandled_server_error",
        path=str(request.url.path),
        error_type=type(exc).__name__,
        error=str(exc),
    )
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Something went wrong. Please try again in a moment."},
        )
    return templates.TemplateResponse(
        request=request,
        name="not-found.html",
        context={"page_title": "Temporary issue"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or new_request_id()
    token = request_id_context.set(request_id)
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        refreshed = getattr(request.state, "refreshed_session", None)
        if refreshed:
            set_session_cookies(response, refreshed)
        return response
    finally:
        request_id_context.reset(token)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "page_title": "Your next learning step, made clear",
            "user": await current_user(request),
        },
    )


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "skillorbit",
        "supabase": "configured" if settings.supabase_configured else "missing",
        "vector": "configured" if settings.vector_configured else "missing",
        "mesh": "configured" if settings.mesh_configured else "missing",
        "digest": "configured" if settings.digest_configured else "missing",
    }


def _verify_cron_secret(request: Request) -> None:
    if not settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRON_SECRET is not configured on the server.",
        )
    provided = request.headers.get("x-cron-secret") or request.query_params.get("secret", "")
    if provided != settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron secret.",
        )


@app.post("/api/cron/weekly-digest", tags=["system"])
async def cron_weekly_digest(request: Request) -> dict[str, int]:
    """Trigger weekly digest delivery (for cron-job.org / external scheduler)."""
    _verify_cron_secret(request)
    if not settings.digest_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weekly digest is not configured (Resend + service role key).",
        )
    try:
        return await run_weekly_digest()
    except DigestError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


@app.get("/api/cron/weekly-digest", tags=["system"])
async def cron_weekly_digest_get(request: Request) -> dict[str, int]:
    """GET alias so simple uptime/cron monitors can trigger the weekly digest check."""
    return await cron_weekly_digest(request)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if user:
        return RedirectResponse(post_auth_destination(profile), status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="auth.html",
        context={
            "page_title": "Sign in",
            "mode": "login",
            "error": request.query_params.get("error"),
            "notice": request.query_params.get("notice"),
            "supabase_configured": settings.supabase_configured,
        },
    )


@app.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if user:
        return RedirectResponse(post_auth_destination(profile), status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="auth.html",
        context={
            "page_title": "Create your account",
            "mode": "signup",
            "error": request.query_params.get("error"),
            "notice": request.query_params.get("notice"),
            "supabase_configured": settings.supabase_configured,
        },
    )


@app.post("/auth/signup", include_in_schema=False)
async def signup_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    try:
        result = await sign_up(email.strip(), password)
    except SupabaseNotConfiguredError:
        return RedirectResponse(
            "/signup?error=Authentication%20is%20not%20configured%20yet.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except HTTPException as error:
        return RedirectResponse(
            f"/signup?error={quote(str(error.detail))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    access_token = result.get("access_token")
    if not access_token:
        return RedirectResponse(
            "/login?notice=Check%20your%20email%20to%20confirm%20your%20account.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    response = RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookies(response, result)
    return response


@app.post("/auth/login", include_in_schema=False)
async def login_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    try:
        result = await sign_in(email.strip(), password)
    except SupabaseNotConfiguredError:
        return RedirectResponse(
            "/login?error=Authentication%20is%20not%20configured%20yet.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except HTTPException as error:
        return RedirectResponse(
            f"/login?error={quote(str(error.detail))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    access_token = result.get("access_token")
    if not access_token:
        return RedirectResponse(
            "/login?error=No%20session%20was%20returned.%20Please%20confirm%20your%20email.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    user_id = (result.get("user") or {}).get("id")
    if not user_id:
        user_obj = await get_user(access_token)
        user_id = user_obj.get("id") if user_obj else None
    profile = await get_profile(access_token, user_id) if user_id else None
    destination = post_auth_destination(profile)
    response = RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookies(response, result)
    return response


@app.post("/auth/logout", include_in_schema=False)
async def logout_action() -> RedirectResponse:
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookies(response)
    return response


@app.get("/onboarding", response_class=HTMLResponse, include_in_schema=False)
async def onboarding_page(request: Request) -> HTMLResponse:
    user = await current_user(request)
    if not user:
        return RedirectResponse("/login?notice=Sign%20in%20to%20set%20your%20learning%20direction.")
    return templates.TemplateResponse(
        request=request,
        name="onboarding.html",
        context={
            "page_title": "Set your learning direction",
            "email": user.get("email", ""),
            "user": user,
            "error": request.query_params.get("error"),
        },
    )


@app.post("/onboarding", include_in_schema=False)
async def onboarding_action(
    request: Request,
    career_goal: str = Form(...),
    current_level: str = Form(...),
    weekly_minutes: int = Form(...),
) -> RedirectResponse:
    access_token, user = await resolve_access_token(request)
    if not user or not access_token:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    try:
        await upsert_profile(
            access_token,
            {
                "user_id": user["id"],
                "career_goal": career_goal,
                "current_level": current_level,
                "weekly_minutes": weekly_minutes,
                "onboarding_complete": True,
            },
        )
        await store_events(
            access_token,
            user["id"],
            ActivityBatch.model_validate({
                "events": [{
                    "event_id": str(uuid4()),
                    "event_type": "learning_goal_updated",
                    "metadata": {
                        "career_goal": career_goal,
                        "current_level": current_level,
                        "weekly_minutes": weekly_minutes,
                    },
                }]
            }),
        )
        await refresh_interest_profile(
            access_token,
            user["id"],
            {
                "user_id": user["id"],
                "career_goal": career_goal,
                "current_level": current_level,
                "weekly_minutes": weekly_minutes,
            },
        )
    except (SupabaseNotConfiguredError, HTTPException) as error:
        detail = getattr(error, "detail", str(error))
        return RedirectResponse(
            f"/onboarding?error={quote(str(detail))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except ActivityError:
        pass
    except InterestProfileError:
        pass
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


def _activity_labels(
    activity: list[dict[str, Any]],
    products_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for event in activity:
        row = dict(event)
        resource_id = event.get("resource_id")
        product = products_by_id.get(resource_id or "")
        if product:
            row["resource_title"] = product.get("title", "")
        enriched.append(row)
    return enriched


async def _products_for_activity(
    access_token: str,
    activity: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    ids = [
        event["resource_id"]
        for event in activity
        if event.get("resource_id")
    ]
    if not ids:
        return {}
    products = await list_products_by_ids(ids[:30])
    return {product["id"]: product for product in products}


def _parse_since_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc) - timedelta(seconds=3)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return datetime.now(timezone.utc) - timedelta(hours=1)


def _sse_message(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


async def _live_event_payloads(
    access_token: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not events:
        return []
    products_by_id = await _products_for_activity(access_token, events)
    enriched = _activity_labels(events, products_by_id)
    return [
        format_live_event(row, resource_title=row.get("resource_title", ""))
        for row in enriched
    ]


@app.get("/bookmarks", response_class=HTMLResponse, include_in_schema=False)
async def bookmarks_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse(
            "/login?notice=Sign%20in%20to%20view%20your%20saved%20library.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    access_token, _ = await resolve_access_token(request)
    products: list[dict] = []
    error = None
    try:
        product_ids = await bookmarked_product_ids(access_token or "", user["id"])
        if product_ids:
            products = await list_products_by_ids(product_ids)
            order = {product_id: index for index, product_id in enumerate(product_ids)}
            products.sort(key=lambda item: order.get(item["id"], 999))
    except (BookmarkError, CatalogError) as bookmark_error:
        error = str(bookmark_error)
    return templates.TemplateResponse(
        request=request,
        name="bookmarks.html",
        context={
            "page_title": "Saved library",
            "user": user,
            "profile": profile,
            "products": products,
            "error": error,
        },
    )


@app.post("/api/progress/{product_id}", tags=["progress"])
async def update_progress(
    request: Request,
    product_id: str,
    progress_status: str = Form(...),
) -> dict[str, str]:
    access_token, user = await _require_api_session(request)
    if progress_status not in {"started", "completed"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid progress status.")
    try:
        await set_progress(access_token, user["id"], product_id, progress_status)  # type: ignore[arg-type]
    except ProgressError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    return {"status": progress_status}


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse("/login?notice=Sign%20in%20to%20open%20your%20dashboard.")
    if profile and not profile.get("onboarding_complete"):
        return RedirectResponse("/onboarding", status_code=status.HTTP_303_SEE_OTHER)
    activity = []
    activity_error = None
    interest_profile = None
    interest_error = None
    recommendation = None
    recommendation_error = None
    recommendation_history_rows = []
    path_intelligence = None
    progress_rows: list[dict] = []
    progress_stats: dict[str, Any] = {}
    path_product_ids: list[str] = []
    learning_streak_days = 0
    weekly_minutes_logged = 0
    weekly_minutes_goal = (profile or {}).get("weekly_minutes") or 0
    access_token, _ = await resolve_access_token(request)
    goal = (profile or {}).get("career_goal") or ""
    token = access_token or ""

    async def _fetch_activity() -> tuple[list[dict[str, Any]], str | None]:
        try:
            return await recent_events(token, user["id"], limit=12), None
        except Exception as error:
            return [], str(error)

    async def _fetch_path_product_ids() -> list[str]:
        if not goal:
            return []
        try:
            path_products = await list_products_for_goal(goal)
            return [product["id"] for product in path_products]
        except Exception:
            return []

    async def _fetch_progress() -> tuple[list[dict], str | None]:
        try:
            return await list_progress(token, user["id"]), None
        except Exception as error:
            return [], str(error)

    async def _fetch_streak() -> int:
        try:
            return await learning_streak(token, user["id"])
        except Exception:
            return 0

    async def _fetch_weekly_minutes() -> int:
        try:
            return await weekly_learning_minutes(token, user["id"])
        except Exception:
            return 0

    async def _fetch_interest() -> tuple[dict[str, Any] | None, str | None]:
        try:
            return await get_interest_profile(token, user["id"]), None
        except Exception as error:
            return None, str(error)

    async def _fetch_recommendation() -> tuple[dict[str, Any] | None, str | None]:
        try:
            return await latest_recommendation(token, user["id"]), None
        except Exception as error:
            return None, str(error)

    async def _fetch_recommendation_history() -> tuple[list[dict], str | None]:
        try:
            return await recommendation_history(token, user["id"]), None
        except Exception as error:
            return [], str(error)

    (
        (activity, activity_error),
        path_product_ids,
        (progress_rows, progress_error),
        learning_streak_days,
        weekly_minutes_logged,
        (interest_profile, interest_error),
        (recommendation, recommendation_error),
        (recommendation_history_rows, history_error),
    ) = await asyncio.gather(
        _fetch_activity(),
        _fetch_path_product_ids(),
        _fetch_progress(),
        _fetch_streak(),
        _fetch_weekly_minutes(),
        _fetch_interest(),
        _fetch_recommendation(),
        _fetch_recommendation_history(),
    )
    if progress_error and not activity_error:
        activity_error = progress_error
    if history_error and not recommendation_error:
        recommendation_error = history_error
    progress_stats = progress_summary(progress_rows, path_product_ids)
    try:
        products_by_id = await _products_for_activity(token, activity)
        activity = _activity_labels(activity, products_by_id)
    except CatalogError:
        pass
    previous_recommendation = (
        recommendation_history_rows[1] if len(recommendation_history_rows) > 1 else None
    )
    try:
        if recommendation:
            rec_path_ids = [
                item["product_id"]
                for item in recommendation.get("items") or []
                if item.get("product_id")
            ]
            rec_progress = progress_summary(progress_rows, rec_path_ids) if rec_path_ids else progress_stats
            path_intelligence = await build_path_intelligence(
                token,
                user["id"],
                profile,
                recommendation,
                interest_profile,
                rec_progress,
                last_recommendation=previous_recommendation,
                lightweight=True,
            )
    except Exception:
        path_intelligence = None
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "page_title": "Your dashboard",
            "email": user.get("email", ""),
            "user": user,
            "profile": profile,
            "activity": activity,
            "activity_error": activity_error,
            "interest_profile": interest_profile,
            "interest_error": interest_error,
            "recommendation": recommendation,
            "recommendation_error": recommendation_error,
            "recommendation_history": recommendation_history_rows,
            "career_goals": CAREER_GOALS,
            "resend_configured": settings.resend_configured,
            "progress_stats": progress_stats,
            "progress_rows": progress_rows,
            "learning_streak_days": learning_streak_days,
            "weekly_minutes_logged": weekly_minutes_logged,
            "weekly_minutes_goal": weekly_minutes_goal,
            "mesh_dashboard_url": "https://developers.meshapi.ai",
            "previous_recommendation": previous_recommendation,
            "path_intelligence": path_intelligence,
        },
    )


async def _require_api_session(request: Request) -> tuple[str, dict[str, Any]]:
    return await require_access_token(request)


async def _admin_access_token(
    request: Request,
) -> tuple[str | None, RedirectResponse | None]:
    """Resolve a fresh token for admin actions; redirect if the session is gone."""
    access_token, user = await resolve_access_token(request)
    if not access_token or not user:
        return None, RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return access_token, None


@app.post("/api/events", tags=["activity"])
async def ingest_activity_events(
    request: Request,
) -> dict[str, Any]:
    access_token, user = await _require_api_session(request)
    try:
        payload = await request.json()
        batch = ActivityBatch.model_validate(payload)
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The activity payload is invalid.",
        ) from error
    try:
        accepted = await store_events(access_token, user["id"], batch)
    except ActivityError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    refresh_recommended = False
    auto_generate = False
    try:
        cached_profile = await get_interest_profile(access_token, user["id"])
        meaningful_in_batch = batch_has_meaningful_events(batch)
        refresh_recommended = bool(
            (cached_profile or {}).get("refresh_recommended")
            or meaningful_in_batch
        )
        if refresh_recommended:
            cached = await latest_recommendation(access_token, user["id"])
            trigger_profile = dict(cached_profile or {})
            if meaningful_in_batch:
                trigger_profile["refresh_recommended"] = True
            auto_generate = should_auto_generate(trigger_profile, cached)
    except (InterestProfileError, RecommendationError):
        pass

    await signal_bus.publish(user["id"], {
        "accepted": accepted,
        "refresh_recommended": refresh_recommended,
        "auto_generate_recommended": auto_generate,
    })

    return {
        "accepted": accepted,
        "refresh_recommended": refresh_recommended,
        "auto_generate_recommended": auto_generate,
    }


@app.get("/api/events/stream", tags=["activity"])
async def events_stream(request: Request) -> StreamingResponse:
    access_token, user = await _require_api_session(request)
    visit_since = _parse_since_timestamp(request.query_params.get("since"))
    seen_event_ids: set[str] = set()
    new_since_visit_announced = False

    async def generator():
        nonlocal new_since_visit_announced
        queue = signal_bus.subscribe(user["id"])
        cursor = datetime.now(timezone.utc) - timedelta(seconds=2)
        poll_tick = 0
        yield _sse_message("connected", {"status": "ok"})
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=2.0)
                    yield _sse_message("ingest", payload)
                    poll_tick = 0
                except asyncio.TimeoutError:
                    pass

                poll_tick += 1
                if poll_tick < 3:
                    continue
                poll_tick = 0

                try:
                    rows = await events_since(access_token, user["id"], cursor, limit=20)
                    live_rows = await _live_event_payloads(access_token, rows)
                    for row in live_rows:
                        event_id = row.get("event_id")
                        if not event_id or event_id in seen_event_ids:
                            continue
                        seen_event_ids.add(str(event_id))
                        occurred_at = row.get("occurred_at")
                        if occurred_at:
                            parsed = _parse_since_timestamp(str(occurred_at))
                            if parsed > cursor:
                                cursor = parsed
                        yield _sse_message("signal", row)

                    interest = await get_interest_profile(access_token, user["id"])
                    visit_rows = await events_since(access_token, user["id"], visit_since, limit=50)
                    new_since_visit = len({
                        row.get("event_id")
                        for row in visit_rows
                        if row.get("event_id")
                    })
                    stats = {
                        "meaningful_event_count": (interest or {}).get("meaningful_event_count", 0),
                        "refresh_recommended": bool((interest or {}).get("refresh_recommended")),
                        "new_since_visit": new_since_visit,
                    }
                    yield _sse_message("stats", stats)
                    if not new_since_visit_announced and new_since_visit > 0:
                        yield _sse_message("visit", {"new_since_visit": new_since_visit})
                        new_since_visit_announced = True
                except (ActivityError, InterestProfileError):
                    yield _sse_message("heartbeat", {"ts": datetime.now(timezone.utc).isoformat()})
        finally:
            signal_bus.unsubscribe(user["id"], queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/interest-profile/refresh", tags=["activity"])
async def refresh_profile_endpoint(request: Request) -> dict:
    access_token, user = await _require_api_session(request)
    _, profile = await current_user_context(request)
    try:
        result = await refresh_interest_profile(access_token, user["id"], profile)
    except InterestProfileError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return {
        "profile_version": result["profile_version"],
        "interest_snapshot": result["interest_snapshot"],
        "meaningful_event_count": result["meaningful_event_count"],
        "refresh_recommended": result["refresh_recommended"],
    }


@app.post("/api/recommendations/generate", tags=["recommendations"])
async def generate_recommendation_endpoint(request: Request) -> dict:
    access_token, user = await _require_api_session(request)
    _, profile = await current_user_context(request)
    force = request.query_params.get("force") == "true"
    last_recommendation = None
    try:
        if not force:
            cached = await latest_recommendation(access_token, user["id"])
            if cached and within_cooldown(cached) and is_recommendation_fresh(cached):
                history_rows = await recommendation_history(access_token, user["id"], limit=2)
                if len(history_rows) > 1:
                    last_recommendation = history_rows[1]
                return await recommendation_api_with_intelligence(
                    access_token,
                    user["id"],
                    profile,
                    cached,
                    cached=True,
                    last_recommendation=last_recommendation,
                )
        history_rows = await recommendation_history(access_token, user["id"], limit=1)
        if history_rows:
            last_recommendation = history_rows[0]
        try:
            await refresh_interest_profile(access_token, user["id"], profile)
        except InterestProfileError:
            pass
        recommendation = await generate_recommendation(
            access_token,
            user["id"],
            profile,
        )
    except RecommendationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except Exception as error:
        log_event(
            logger,
            logging.ERROR,
            "recommendation_generate_failed",
            error_type=type(error).__name__,
            error=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A grounded recommendation could not be produced.",
        ) from error
    return await recommendation_api_with_intelligence(
        access_token,
        user["id"],
        profile,
        recommendation,
        cached=False,
        last_recommendation=last_recommendation,
    )


@app.post("/api/recommendations/{recommendation_id}/feedback", tags=["recommendations"])
async def recommendation_feedback(
    recommendation_id: str,
    request: Request,
) -> dict[str, str]:
    access_token, user = await _require_api_session(request)
    try:
        UUID(recommendation_id)
        payload = await request.json()
        feedback = payload.get("feedback")
        if feedback not in {"useful", "not_relevant"}:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Feedback must be useful or not_relevant.",
        )
    event = ActivityBatch.model_validate({
        "events": [{
            "event_id": str(uuid4()),
            "event_type": "recommendation_feedback",
            "metadata": {
                "recommendation_id": recommendation_id,
                "feedback": feedback,
            },
        }]
    })
    feedback_influence = None
    try:
        owned = await latest_recommendation(access_token, user["id"])
        if not owned or owned.get("id") != recommendation_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recommendation not found.",
            )
        categories = await recommendation_item_categories(access_token, recommendation_id)
        event.events[0].metadata["categories"] = categories
        feedback_influence = await apply_recommendation_feedback(
            access_token,
            user["id"],
            categories,
            feedback,
        )
        await store_events(access_token, user["id"], event)
        await update_recommendation_status(
            access_token,
            user["id"],
            recommendation_id,
            "dismissed" if feedback == "not_relevant" else "active",
        )
    except (ActivityError, RecommendationError, InterestProfileError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback could not be saved right now.",
        ) from error
    return {
        "status": "saved",
        "feedback_influence": feedback_influence,
    }


@app.post("/api/recommendations/{recommendation_id}/email", tags=["recommendations"])
async def email_recommendation(
    recommendation_id: str,
    request: Request,
) -> dict[str, str]:
    access_token, user = await _require_api_session(request)
    try:
        UUID(recommendation_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That recommendation ID is invalid.",
        ) from error
    recipient_email = user.get("email")
    if not recipient_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Your account does not have an email address.",
        )
    try:
        result = await send_recommendation_email(
            access_token,
            user["id"],
            recipient_email,
            recommendation_id,
            recipient_name=recipient_email.split("@", 1)[0],
        )
    except EmailDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return {"status": result["status"], "delivery_id": result.get("delivery_id") or ""}


@app.get("/explore", response_class=HTMLResponse, include_in_schema=False)
async def explore_page(
    request: Request,
    search: str = "",
    category: str = "",
    difficulty: str = "",
    content_type: str = "",
    career_goal: str = "",
) -> HTMLResponse:
    error = None
    products: list[dict] = []
    try:
        if search.strip():
            product_ids = await semantic_product_ids(search.strip(), limit=12)
            products = await list_products_by_ids(
                product_ids,
                category=category.strip(),
                difficulty=difficulty.strip(),
            )
        else:
            products = await list_products(
                category=category.strip(),
                difficulty=difficulty.strip(),
                content_type=content_type.strip(),
                career_goal=career_goal.strip(),
            )
    except CatalogError as catalog_error:
        error = str(catalog_error)
    except VectorSyncError as vector_error:
        error = str(vector_error)
    except Exception as search_error:
        log_event(
            logger,
            logging.ERROR,
            "explore_search_failed",
            search=search.strip(),
            error_type=type(search_error).__name__,
            error=str(search_error),
        )
        error = "Search is temporarily unavailable. Try again or browse without search."
        products = []
    categories = sorted({product.get("category", "") for product in products if product.get("category")})
    _, profile = await current_user_context(request)
    return templates.TemplateResponse(
        request=request,
        name="explore.html",
        context={
            "page_title": "Explore learning resources",
            "user": await current_user(request),
            "profile": profile,
            "products": products,
            "categories": categories,
            "search": search,
            "category": category,
            "difficulty": difficulty,
            "content_type": content_type,
            "career_goal": career_goal,
            "career_goals": CAREER_GOALS,
            "error": error,
        },
    )


@app.get("/learning-path", response_class=HTMLResponse, include_in_schema=False)
async def learning_path_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse(
            "/login?notice=Sign%20in%20to%20view%20your%20learning%20path.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    goal = (profile or {}).get("career_goal") or ""
    path_products: list[dict] = []
    path_error = None
    progress_rows: list[dict] = []
    progress_stats: dict[str, Any] = {}
    access_token, _ = await resolve_access_token(request)
    try:
        if goal:
            path_products = await list_products_for_goal(goal)
            path_ids = [product["id"] for product in path_products]
            progress_rows = await list_progress(access_token or "", user["id"])
            progress_stats = progress_summary(progress_rows, path_ids)
    except CatalogError as error:
        path_error = str(error)
    except ProgressError:
        pass
    completed_ids = {
        row["product_id"] for row in progress_rows if row.get("status") == "completed"
    }
    return templates.TemplateResponse(
        request=request,
        name="learning-path.html",
        context={
            "page_title": "Your learning path",
            "user": user,
            "profile": profile,
            "career_goal": goal,
            "path_products": path_products,
            "career_goals": CAREER_GOALS,
            "path_error": path_error,
            "progress_stats": progress_stats,
            "completed_ids": completed_ids,
        },
    )


@app.get("/recommendations", response_class=HTMLResponse, include_in_schema=False)
async def recommendations_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse(
            "/login?notice=Sign%20in%20to%20view%20recommendations.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    access_token, _ = await resolve_access_token(request)
    recommendation = None
    history_rows: list[dict] = []
    previous_recommendation = None
    error = None
    try:
        recommendation = await latest_recommendation(access_token or "", user["id"])
        history_rows = await recommendation_history(access_token or "", user["id"], limit=10)
        if len(history_rows) > 1:
            previous_recommendation = history_rows[1]
    except RecommendationError as rec_error:
        error = str(rec_error)
    return templates.TemplateResponse(
        request=request,
        name="recommendations.html",
        context={
            "page_title": "Your recommendations",
            "user": user,
            "profile": profile,
            "recommendation": recommendation,
            "previous_recommendation": previous_recommendation,
            "recommendation_history": history_rows,
            "error": error,
            "mesh_dashboard_url": "https://developers.meshapi.ai",
        },
    )


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def judge_demo_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    is_admin = bool(profile and profile.get("role") == "admin")
    return templates.TemplateResponse(
        request=request,
        name="demo.html",
        context={
            "page_title": "Judge demo",
            "user": user,
            "profile": profile,
            "demo_steps": DEMO_STEPS,
            "is_admin": is_admin,
            "app_public_url": settings.app_public_url.rstrip("/"),
            "mesh_dashboard_url": "https://developers.meshapi.ai",
        },
    )


@app.post("/api/admin/demo-seed", tags=["admin"])
async def admin_demo_seed_endpoint(request: Request) -> dict[str, Any]:
    user, profile = await current_user_context(request)
    if not user or not profile or profile.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    try:
        return await apply_demo_seed(email=user.get("email"))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


@app.get("/path/{recommendation_id}", response_class=HTMLResponse, include_in_schema=False)
async def shareable_path_page(request: Request, recommendation_id: str) -> HTMLResponse:
    try:
        UUID(recommendation_id)
    except ValueError:
        return templates.TemplateResponse(
            request=request,
            name="path-share.html",
            context={
                "page_title": "Learning path",
                "path": None,
                "error": "That link is not valid.",
                "mesh_dashboard_url": "https://developers.meshapi.ai",
                "app_public_url": settings.app_public_url.rstrip("/"),
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    path = None
    error = None
    try:
        path = await get_recommendation_for_share(recommendation_id)
        if not path:
            error = "This learning path is no longer available."
    except RecommendationError as rec_error:
        error = str(rec_error)
    return templates.TemplateResponse(
        request=request,
        name="path-share.html",
        context={
            "page_title": "Shared learning path",
            "path": path,
            "error": error,
            "mesh_dashboard_url": "https://developers.meshapi.ai",
            "app_public_url": settings.app_public_url.rstrip("/"),
        },
        status_code=status.HTTP_404_NOT_FOUND if error and not path else status.HTTP_200_OK,
    )


@app.get("/path/{recommendation_id}/print", response_class=HTMLResponse, include_in_schema=False)
async def shareable_path_print_page(request: Request, recommendation_id: str) -> HTMLResponse:
    try:
        UUID(recommendation_id)
    except ValueError:
        return templates.TemplateResponse(
            request=request,
            name="path-export.html",
            context={
                "page_title": "Learning path export",
                "path": None,
                "error": "That link is not valid.",
                "auto_print": False,
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    path = None
    error = None
    try:
        path = await get_recommendation_for_share(recommendation_id)
        if not path:
            error = "This learning path is no longer available."
    except RecommendationError as rec_error:
        error = str(rec_error)
    auto_print = request.query_params.get("print", "1") != "0"
    return templates.TemplateResponse(
        request=request,
        name="path-export.html",
        context={
            "page_title": "Learning path export",
            "path": path,
            "error": error,
            "auto_print": auto_print and bool(path),
        },
        status_code=status.HTTP_404_NOT_FOUND if error and not path else status.HTTP_200_OK,
    )


@app.get("/trace", response_class=HTMLResponse, include_in_schema=False)
async def recommendation_trace_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse(
            "/login?notice=Sign%20in%20to%20view%20recommendation%20traces.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    access_token, _ = await resolve_access_token(request)
    recommendation = None
    history_rows: list[dict] = []
    error = None
    try:
        recommendation = await latest_recommendation(access_token or "", user["id"])
        history_rows = await recommendation_history(access_token or "", user["id"], limit=5)
    except RecommendationError as rec_error:
        error = str(rec_error)
    return templates.TemplateResponse(
        request=request,
        name="trace.html",
        context={
            "page_title": "Recommendation trace",
            "user": user,
            "profile": profile,
            "recommendation": recommendation,
            "recommendation_history": history_rows,
            "error": error,
            "mesh_dashboard_url": "https://developers.meshapi.ai",
        },
    )


@app.get("/resource/{product_id}", response_class=HTMLResponse, include_in_schema=False)
async def resource_detail(request: Request, product_id: str) -> HTMLResponse:
    try:
        product = await get_product(product_id)
    except CatalogError as catalog_error:
        return templates.TemplateResponse(
            request=request,
            name="not-ready.html",
            context={
                "page_title": "Catalog not ready",
                "message": str(catalog_error),
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not product:
        return templates.TemplateResponse(
            request=request,
            name="not-found.html",
            context={"page_title": "Resource not found"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    user_obj = await current_user(request)
    activity: list[dict] = []
    recommendation_snippet = None
    if user_obj:
        access_token, _ = await resolve_access_token(request)
        try:
            activity = await recent_events(access_token or "", user_obj["id"], limit=8)
            products_by_id = await _products_for_activity(access_token or "", activity)
            activity = _activity_labels(activity, products_by_id)
        except (ActivityError, CatalogError):
            activity = []
        try:
            recommendation_snippet = await latest_recommendation(access_token or "", user_obj["id"])
        except RecommendationError:
            recommendation_snippet = None
    return templates.TemplateResponse(
        request=request,
        name="resource-detail.html",
        context={
            "page_title": product["title"],
            "user": user_obj,
            "profile": (await current_user_context(request))[1],
            "product": product,
            "activity": activity,
            "recommendation_snippet": recommendation_snippet,
        },
    )


@app.get("/admin/products", response_class=HTMLResponse, include_in_schema=False)
async def admin_products_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse(
            "/login?notice=Sign%20in%20to%20manage%20the%20catalog.",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not profile or profile.get("role") != "admin":
        return templates.TemplateResponse(
            request=request,
            name="not-found.html",
            context={"page_title": "Admin access required"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    try:
        products = await admin_list_products(access_token)
    except CatalogError as catalog_error:
        return templates.TemplateResponse(
            request=request,
            name="admin-products.html",
            context={
                "page_title": "Admin catalog",
                "products": [],
                "error": str(catalog_error),
                "message": None,
            },
        )
    return templates.TemplateResponse(
        request=request,
        name="admin-products.html",
        context={
            "page_title": "Admin catalog",
            "products": products,
            "error": None,
            "message": request.query_params.get("message"),
        },
    )


@app.post("/admin/products/index", include_in_schema=False)
async def admin_index_products(request: Request) -> RedirectResponse:
    user, profile = await current_user_context(request)
    if not user or not profile or profile.get("role") != "admin":
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    indexed = 0
    failed = 0
    try:
        products = await admin_list_products(access_token)
        for product in products:
            if not product.get("is_active"):
                continue
            if product.get("vector_sync_status") == "synced":
                continue
            if await _sync_product(access_token, product):
                indexed += 1
            else:
                failed += 1
    except CatalogError as catalog_error:
        return RedirectResponse(
            f"/admin/products?message={quote(str(catalog_error))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    message = f"Indexed {indexed} resource(s)."
    if failed:
        message += f" {failed} resource(s) need a retry."
    return RedirectResponse(
        f"/admin/products?message={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/products/new", response_class=HTMLResponse, include_in_schema=False)
async def admin_new_product_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not profile or profile.get("role") != "admin":
        return templates.TemplateResponse(
            request=request,
            name="not-found.html",
            context={"page_title": "Admin access required"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return templates.TemplateResponse(
        request=request,
        name="admin-product-form.html",
        context={"page_title": "Add resource", "product": None, "error": None},
    )


@app.post("/admin/products/new", include_in_schema=False, response_model=None)
async def admin_create_product_action(
    request: Request,
    title: str = Form(...),
    slug: str = Form(...),
    short_summary: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    provider: str = Form(...),
    content_type: str = Form(...),
    difficulty: str = Form(...),
    duration_minutes: int = Form(...),
    source_url: str = Form(""),
    license_info: str = Form(...),
    skills: str = Form(...),
    career_goals: str = Form(...),
    prerequisites: str = Form(""),
    learning_outcomes: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    user, profile = await current_user_context(request)
    if not user or not profile or profile.get("role") != "admin":
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    payload = _product_payload(
        title=title,
        slug=slug,
        short_summary=short_summary,
        description=description,
        category=category,
        provider=provider,
        content_type=content_type,
        difficulty=difficulty,
        duration_minutes=duration_minutes,
        source_url=source_url,
        license_info=license_info,
        skills=skills,
        career_goals=career_goals,
        prerequisites=prerequisites,
        learning_outcomes=learning_outcomes,
    )
    try:
        product = await admin_create_product(access_token, payload)
    except CatalogError as catalog_error:
        return templates.TemplateResponse(
            request=request,
            name="admin-product-form.html",
            context={
                "page_title": "Add resource",
                "product": payload,
                "error": str(catalog_error),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    indexed = await _sync_product(access_token, product)
    message = "Resource created and indexed." if indexed else "Resource created; vector indexing needs a retry."
    return RedirectResponse(
        f"/admin/products?message={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/products/{product_id}/edit", response_class=HTMLResponse, include_in_schema=False)
async def admin_edit_product_page(request: Request, product_id: str) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not profile or profile.get("role") != "admin":
        return templates.TemplateResponse(
            request=request,
            name="not-found.html",
            context={"page_title": "Admin access required"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    try:
        product = await admin_get_product(
            access_token,
            product_id,
        )
    except CatalogError as catalog_error:
        return templates.TemplateResponse(
            request=request,
            name="not-ready.html",
            context={"page_title": "Catalog not ready", "message": str(catalog_error)},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not product:
        return templates.TemplateResponse(
            request=request,
            name="not-found.html",
            context={"page_title": "Resource not found"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request=request,
        name="admin-product-form.html",
        context={"page_title": "Edit resource", "product": product, "error": None},
    )


@app.post("/admin/products/{product_id}/edit", include_in_schema=False, response_model=None)
async def admin_edit_product_action(
    request: Request,
    product_id: str,
    title: str = Form(...),
    slug: str = Form(...),
    short_summary: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    provider: str = Form(...),
    content_type: str = Form(...),
    difficulty: str = Form(...),
    duration_minutes: int = Form(...),
    source_url: str = Form(""),
    license_info: str = Form(...),
    skills: str = Form(...),
    career_goals: str = Form(...),
    prerequisites: str = Form(""),
    learning_outcomes: str = Form(...),
) -> RedirectResponse | HTMLResponse:
    user, profile = await current_user_context(request)
    if not user or not profile or profile.get("role") != "admin":
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    payload = _product_payload(
        title=title,
        slug=slug,
        short_summary=short_summary,
        description=description,
        category=category,
        provider=provider,
        content_type=content_type,
        difficulty=difficulty,
        duration_minutes=duration_minutes,
        source_url=source_url,
        license_info=license_info,
        skills=skills,
        career_goals=career_goals,
        prerequisites=prerequisites,
        learning_outcomes=learning_outcomes,
    )
    try:
        product = await admin_update_product(access_token, product_id, payload)
    except CatalogError as catalog_error:
        return templates.TemplateResponse(
            request=request,
            name="admin-product-form.html",
            context={
                "page_title": "Edit resource",
                "product": {**payload, "id": product_id},
                "error": str(catalog_error),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    indexed = await _sync_product(access_token, product)
    message = "Resource updated and indexed." if indexed else "Resource updated; vector indexing needs a retry."
    return RedirectResponse(
        f"/admin/products?message={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/products/{product_id}/deactivate", include_in_schema=False)
async def admin_deactivate_product(
    request: Request,
    product_id: str,
) -> RedirectResponse:
    user, profile = await current_user_context(request)
    if not user or not profile or profile.get("role") != "admin":
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    try:
        await admin_update_product(
            access_token,
            product_id,
            {"is_active": False},
        )
    except CatalogError as catalog_error:
        return RedirectResponse(
            f"/admin/products?message={quote(str(catalog_error))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        await delete_product_vector(product_id)
        await admin_update_vector_status(
            access_token,
            product_id,
            status="pending",
            error=None,
        )
    except VectorSyncError as vector_error:
        await admin_update_vector_status(
            access_token,
            product_id,
            status="failed",
            error=str(vector_error),
        )
        return RedirectResponse(
            f"/admin/products?message={quote('Resource deactivated, but vector removal needs a retry.')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        "/admin/products?message=Resource%20deactivated.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/admin/products/{product_id}/reactivate", include_in_schema=False)
async def admin_reactivate_product(
    request: Request,
    product_id: str,
) -> RedirectResponse:
    user, profile = await current_user_context(request)
    if not user or not profile or profile.get("role") != "admin":
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    try:
        product = await admin_update_product(
            access_token,
            product_id,
            {"is_active": True, "vector_sync_status": "pending"},
        )
    except CatalogError as catalog_error:
        return RedirectResponse(
            f"/admin/products?message={quote(str(catalog_error))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    indexed = await _sync_product(access_token, product)
    message = "Resource reactivated and indexed." if indexed else "Resource reactivated; vector indexing needs a retry."
    return RedirectResponse(
        f"/admin/products?message={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/admin/sync-health", response_class=HTMLResponse, include_in_schema=False)
async def admin_sync_health_page(request: Request) -> HTMLResponse:
    user, profile = await current_user_context(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not profile or profile.get("role") != "admin":
        return templates.TemplateResponse(
            request=request,
            name="not-found.html",
            context={"page_title": "Admin access required"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    access_token, redirect = await _admin_access_token(request)
    if redirect:
        return redirect
    summary: dict[str, int] = {}
    products: list[dict] = []
    error = None
    try:
        products = await admin_list_products(access_token)
        summary = await sync_health_summary(access_token)
    except CatalogError as catalog_error:
        error = str(catalog_error)
    problem_rows = [
        p for p in products
        if p.get("is_active") and p.get("vector_sync_status") != "synced"
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin-sync-health.html",
        context={
            "page_title": "Vector sync health",
            "user": user,
            "profile": profile,
            "summary": summary,
            "problem_rows": problem_rows[:20],
            "error": error,
        },
    )


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _product_payload(**values: object) -> dict[str, object]:
    payload = dict(values)
    for field in ("skills", "career_goals", "prerequisites", "learning_outcomes"):
        payload[field] = _split_csv(str(payload[field]))
    payload["source_url"] = str(payload["source_url"]).strip() or None
    payload["is_external"] = bool(payload["source_url"])
    payload["price_label"] = "Free resource"
    payload["vector_sync_status"] = "pending"
    return payload


async def _sync_product(access_token: str, product: dict) -> bool:
    attempts = int(product.get("vector_sync_attempts") or 0) + 1
    try:
        await admin_update_vector_status(
            access_token,
            product["id"],
            status="pending",
            error=None,
            attempts=attempts,
        )
        await upsert_product(product)
        await admin_update_vector_status(
            access_token,
            product["id"],
            status="synced",
            error=None,
            attempts=attempts,
            synced_at=datetime.now(timezone.utc).isoformat(),
        )
        return True
    except (CatalogError, VectorSyncError) as error:
        try:
            await admin_update_vector_status(
                access_token,
                product["id"],
                status="failed",
                error=str(error),
                attempts=attempts,
            )
        except CatalogError:
            pass
        return False