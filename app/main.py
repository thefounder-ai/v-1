from datetime import datetime, timezone
from contextlib import asynccontextmanager
import logging
from typing import Any
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.activity import ActivityBatch, ActivityError, recent_events, store_events
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
    get_interest_profile,
    refresh_interest_profile,
)
from app.recommendations import (
    RecommendationError,
    generate_recommendation,
    latest_recommendation,
    recommendation_api_payload,
    recommendation_history,
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

app = FastAPI(
    title="SkillOrbit",
    description="An AI career learning navigator powered by behavioral signals.",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
    }


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
    progress_rows: list[dict] = []
    progress_stats: dict[str, Any] = {}
    path_product_ids: list[str] = []
    learning_streak_days = 0
    weekly_minutes_logged = 0
    weekly_minutes_goal = (profile or {}).get("weekly_minutes") or 0
    access_token, _ = await resolve_access_token(request)
    goal = (profile or {}).get("career_goal") or ""
    try:
        activity = await recent_events(access_token or "", user["id"], limit=12)
    except ActivityError as error:
        activity_error = str(error)
    try:
        if goal:
            path_products = await list_products_for_goal(goal)
            path_product_ids = [product["id"] for product in path_products]
    except CatalogError:
        path_product_ids = []
    try:
        progress_rows = await list_progress(access_token or "", user["id"])
        progress_stats = progress_summary(progress_rows, path_product_ids)
        learning_streak_days = await learning_streak(access_token or "", user["id"])
        weekly_minutes_logged = await weekly_learning_minutes(access_token or "", user["id"])
    except ProgressError as error:
        if not activity_error:
            activity_error = str(error)
    try:
        products_by_id = await _products_for_activity(access_token or "", activity)
        activity = _activity_labels(activity, products_by_id)
    except CatalogError:
        pass
    try:
        interest_profile = await get_interest_profile(access_token or "", user["id"])
    except InterestProfileError as error:
        interest_error = str(error)
    try:
        recommendation = await latest_recommendation(access_token or "", user["id"])
    except RecommendationError as error:
        recommendation_error = str(error)
    try:
        recommendation_history_rows = await recommendation_history(
            access_token or "", user["id"]
        )
    except RecommendationError as error:
        if not recommendation_error:
            recommendation_error = str(error)
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
            "previous_recommendation": recommendation_history_rows[1] if len(recommendation_history_rows) > 1 else None,
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
    _, profile = await current_user_context(request)
    try:
        interest = await refresh_interest_profile(access_token, user["id"], profile)
        refresh_recommended = bool(interest.get("refresh_recommended"))
        if refresh_recommended:
            cached = await latest_recommendation(access_token, user["id"])
            auto_generate = should_auto_generate(interest, cached)
    except (InterestProfileError, RecommendationError):
        pass

    return {
        "accepted": accepted,
        "refresh_recommended": refresh_recommended,
        "auto_generate_recommended": auto_generate,
    }


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
    try:
        if not force:
            cached = await latest_recommendation(access_token, user["id"])
            if cached and within_cooldown(cached) and is_recommendation_fresh(cached):
                return await recommendation_api_payload(cached, cached=True)
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
    return await recommendation_api_payload(recommendation, cached=False)


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
    try:
        await store_events(access_token, user["id"], event)
        await update_recommendation_status(
            access_token,
            user["id"],
            recommendation_id,
            "dismissed" if feedback == "not_relevant" else "active",
        )
    except (ActivityError, RecommendationError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback could not be saved right now.",
        ) from error
    return {"status": "saved"}


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