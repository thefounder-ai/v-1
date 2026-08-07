from typing import Any

import httpx
from fastapi import HTTPException, Request, Response, status

from app.config import settings

SESSION_COOKIE = "skillorbit_session"
REFRESH_COOKIE = "skillorbit_refresh"
SUPABASE_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
SESSION_MAX_AGE = 60 * 60 * 24 * 30


class SupabaseNotConfiguredError(RuntimeError):
    """Raised when auth is requested before Supabase is configured."""


def _require_configured() -> None:
    if not settings.supabase_configured:
        raise SupabaseNotConfiguredError(
            "Supabase Auth is not configured. Add SUPABASE_URL and SUPABASE_ANON_KEY."
        )


def _headers(access_token: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": settings.supabase_anon_key,
        "Content-Type": "application/json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _cookie_kwargs() -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": settings.app_env == "production",
        "samesite": "lax",
        "path": "/",
    }


def set_session_cookies(response: Response, session: dict[str, Any]) -> None:
    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    if not access_token:
        return
    response.set_cookie(
        SESSION_COOKIE,
        access_token,
        max_age=SESSION_MAX_AGE,
        **(_cookie_kwargs()),
    )
    if refresh_token:
        response.set_cookie(
            REFRESH_COOKIE,
            refresh_token,
            max_age=SESSION_MAX_AGE,
            **(_cookie_kwargs()),
        )


def clear_session_cookies(response: Response) -> None:
    kwargs = _cookie_kwargs()
    response.delete_cookie(SESSION_COOKIE, path="/", samesite=kwargs["samesite"])
    response.delete_cookie(REFRESH_COOKIE, path="/", samesite=kwargs["samesite"])


def normalize_auth_session(body: dict[str, Any]) -> dict[str, Any]:
    """Flatten Supabase auth responses whether tokens are top-level or nested."""
    if not body:
        return {}
    if body.get("access_token"):
        return body
    session = body.get("session")
    if isinstance(session, dict) and session.get("access_token"):
        merged = dict(body)
        merged.update(session)
        return merged
    return body


async def sign_up(email: str, password: str) -> dict[str, Any]:
    _require_configured()
    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        response = await client.post(
            f"{settings.supabase_url}/auth/v1/signup",
            headers=_headers(),
            json={"email": email, "password": password},
        )
    body = normalize_auth_session(_parse_response(response))
    if body.get("access_token") and body.get("user"):
        await _ensure_profile(body["access_token"], body["user"]["id"])
    return body


async def sign_in(email: str, password: str) -> dict[str, Any]:
    _require_configured()
    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        response = await client.post(
            f"{settings.supabase_url}/auth/v1/token?grant_type=password",
            headers=_headers(),
            json={"email": email, "password": password},
        )
    body = normalize_auth_session(_parse_response(response))
    if body.get("access_token") and body.get("user"):
        await _ensure_profile(body["access_token"], body["user"]["id"])
    return body


async def refresh_session(refresh_token: str) -> dict[str, Any] | None:
    if not settings.supabase_configured or not refresh_token:
        return None
    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        response = await client.post(
            f"{settings.supabase_url}/auth/v1/token?grant_type=refresh_token",
            headers=_headers(),
            json={"refresh_token": refresh_token},
        )
    if response.is_error:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not body.get("access_token"):
        return None
    return normalize_auth_session(body)


async def get_user(access_token: str) -> dict[str, Any] | None:
    if not settings.supabase_configured:
        return None
    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers=_headers(access_token),
        )
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        return None
    if response.is_error:
        return None
    return response.json()


async def resolve_access_token(request: Request) -> tuple[str | None, dict[str, Any] | None]:
    """Return a valid access token, refreshing the session when needed."""
    access_token = request.cookies.get(SESSION_COOKIE)
    if access_token:
        user = await get_user(access_token)
        if user:
            return access_token, user

    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        return None, None

    session = await refresh_session(refresh_token)
    if not session:
        return None, None

    new_access = session.get("access_token")
    if not new_access:
        return None, None

    user = await get_user(new_access)
    if not user:
        return None, None

    request.state.refreshed_session = session
    return new_access, user


async def upsert_profile(access_token: str, payload: dict[str, Any]) -> None:
    _require_configured()
    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        response = await client.post(
            f"{settings.supabase_url}/rest/v1/profiles?on_conflict=user_id",
            headers={
                **_headers(access_token),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json=payload,
        )
    if response.is_error:
        detail = "Your profile could not be saved. Please try again."
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("message"):
                detail = str(body["message"])
        except ValueError:
            pass
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )


async def get_profile(access_token: str, user_id: str) -> dict[str, Any] | None:
    _require_configured()
    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        response = await client.get(
            f"{settings.supabase_url}/rest/v1/profiles",
            headers=_headers(access_token),
            params={"select": "*", "user_id": f"eq.{user_id}", "limit": "1"},
        )
    if response.is_error:
        return None
    rows = response.json()
    return rows[0] if rows else None


async def _ensure_profile(access_token: str, user_id: str) -> None:
    existing = await get_profile(access_token, user_id)
    if existing:
        return
    try:
        await upsert_profile(
            access_token,
            {
                "user_id": user_id,
                "role": "learner",
                "onboarding_complete": False,
            },
        )
    except HTTPException:
        # DB trigger (migration 011) may have created the row between check and insert.
        if not await get_profile(access_token, user_id):
            raise


async def current_user_context(request: Request) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    access_token, user = await resolve_access_token(request)
    if not user or not access_token:
        return None, None
    return user, await get_profile(access_token, user["id"])


async def current_user(request: Request) -> dict[str, Any] | None:
    access_token, user = await resolve_access_token(request)
    return user


def post_auth_destination(profile: dict[str, Any] | None) -> str:
    if profile and profile.get("onboarding_complete"):
        return "/dashboard"
    return "/onboarding"


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.is_error:
        message = (
            body.get("error_description")
            or body.get("msg")
            or body.get("message")
            or body.get("error")
        )
        if isinstance(message, dict):
            message = message.get("message")
        detail = str(message or "Authentication could not complete this request.")
        if "email not confirmed" in detail.lower():
            detail = "Confirm your email in Supabase, then sign in."
        elif "invalid login credentials" in detail.lower():
            detail = "That email or password is incorrect."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )
    return body


def require_user_or_redirect(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in is required.",
        )
    return user


async def require_access_token(request: Request) -> tuple[str, dict[str, Any]]:
    """Return a valid access token and user, refreshing the session when needed."""
    access_token, user = await resolve_access_token(request)
    if not access_token or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in is required.",
        )
    return access_token, user
