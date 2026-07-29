"""HTTP routes for the optional, recommendation-only Omakase Lite accounts."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from omakase.lite import anilist, auth, credentials, db
from omakase.lite.models import AccountUser

page_router = APIRouter(prefix="/account")
api_router = APIRouter(prefix="/api/account")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _account_asset_version() -> str:
    static_dir = Path(__file__).resolve().parents[1] / "web" / "static"
    assets = (
        static_dir / "account.css",
        static_dir / "account.js",
        static_dir / "account_state.js",
    )
    try:
        return str(max(int(asset.stat().st_mtime) for asset in assets))
    except OSError:
        return "1"


templates.env.globals["account_asset_version"] = _account_asset_version()

_COOKIE_NAME = "omakase_account"
_rate_lock = threading.Lock()
_rate_events: dict[tuple[str, str], deque[float]] = defaultdict(deque)


class ProviderKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: SecretStr


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["neutral", "not_interested", "saved", "watched"]
    watched_score: int | None = Field(default=None, ge=1, le=10, strict=True)


def reset_rate_limits() -> None:
    """Clear in-memory limits. Exposed for isolated tests."""
    with _rate_lock:
        _rate_events.clear()


def _check_rate_limit(
    request: Request,
    *,
    action: str,
    limit: int,
    window_seconds: int,
) -> None:
    address = request.client.host if request.client else "unknown"
    if os.getenv("OMAKASE_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            address = forwarded.split(",", 1)[0].strip()
    key = (action, address)
    now = time.monotonic()
    cutoff = now - window_seconds
    with _rate_lock:
        events = _rate_events[key]
        while events and events[0] < cutoff:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(status_code=429, detail="Please wait before trying again.")
        events.append(now)


def enforce_rate_limit(
    request: Request,
    *,
    action: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Apply the shared in-process limiter to non-account public routes."""
    _check_rate_limit(
        request,
        action=action,
        limit=limit,
        window_seconds=window_seconds,
    )


def _validate_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    if origin == "null":
        if request.headers.get("sec-fetch-site", "").lower() == "same-origin":
            return
        raise HTTPException(status_code=403, detail="Request origin was not accepted.")

    def normalized(value: str) -> tuple[str, str, int | None] | None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        if port == (443 if parsed.scheme == "https" else 80):
            port = None
        return parsed.scheme, parsed.hostname.lower(), port

    submitted = normalized(origin)
    allowed = {normalized(str(request.url))}
    if os.getenv("OMAKASE_TRUST_PROXY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        allowed.add(normalized(_public_url()))
    if submitted is None or submitted not in allowed:
        raise HTTPException(status_code=403, detail="Request origin was not accepted.")


def _secure_cookie() -> bool:
    return os.getenv("OMAKASE_ACCOUNT_SECURE_COOKIE", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _public_url() -> str:
    return os.getenv("OMAKASE_PUBLIC_URL", "https://omakase.jhinx.dev").rstrip("/")


def _anilist_client_secret() -> str:
    secret_file = os.getenv("OMAKASE_ANILIST_CLIENT_SECRET_FILE", "").strip()
    if secret_file:
        try:
            return Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    hosted = os.getenv("OMAKASE_PUBLIC_HOSTED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if hosted:
        return ""
    return os.getenv("OMAKASE_ANILIST_CLIENT_SECRET", "").strip()


def _anilist_media_id(url: str | None) -> int | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"anilist.co", "www.anilist.co"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "anime":
        return None
    try:
        media_id = int(parts[1])
    except ValueError:
        return None
    return media_id if media_id > 0 else None


def _sync_watched_to_anilist(
    conn,
    *,
    user_id: int,
    recommendation_id: int,
    source_username: str,
    recommendation_url: str | None,
    watched_score: int,
    connected_username: str,
    access_token: str | None,
) -> dict[str, str]:
    remote_entry_id = None
    if source_username.strip().casefold() != connected_username.strip().casefold():
        state = "account_mismatch"
        detail = (
            f"This menu used {source_username}, but AniList is connected as "
            f"{connected_username}. No AniList list was changed."
        )
    else:
        media_id = _anilist_media_id(recommendation_url)
        if media_id is None:
            state = "unavailable"
            detail = "Saved in Omakase, but this recommendation has no verified AniList ID to sync."
        elif not access_token:
            state = "failed"
            detail = (
                "Saved in Omakase, but its AniList connection could not be opened. "
                "Disconnect and reconnect AniList."
            )
        else:
            try:
                remote = anilist.save_completed_entry(
                    access_token,
                    media_id,
                    score_ten=watched_score,
                )
                remote_entry_id = int(remote["id"])
                state = "synced"
                detail = (
                    f"Added to {connected_username}’s AniList as Completed · {watched_score}/10."
                )
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                state = "failed"
                detail = (
                    "Saved in Omakase, but AniList did not confirm the list update. "
                    "Try Already watched again."
                )
    db.set_recommendation_tracker_sync(
        conn,
        user_id=user_id,
        recommendation_id=recommendation_id,
        state=state,
        detail=detail,
        remote_entry_id=remote_entry_id,
    )
    return {"state": state, "detail": detail}


def _set_session_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        _COOKIE_NAME,
        token,
        max_age=auth.SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )


def _session_user(request: Request, conn) -> AccountUser | None:
    return auth.validate_session(conn, request.cookies.get(_COOKIE_NAME, ""))


def request_user(request: Request, *, require_csrf: bool = False) -> AccountUser | None:
    """Return the signed-in Lite user and optionally validate a mutating request."""
    conn = db.connect()
    try:
        user = _session_user(request, conn)
        if user is not None and require_csrf:
            _validate_csrf(request, conn, request.headers.get("X-CSRF-Token", ""))
        return user
    finally:
        conn.close()


def optional_user(request: Request) -> AccountUser | None:
    """Return the signed-in Lite user, if any, without leaking a DB handle."""
    return request_user(request)


def _require_user(request: Request, conn) -> AccountUser:
    user = _session_user(request, conn)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def _require_admin(request: Request, conn) -> AccountUser:
    user = _require_user(request, conn)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Owner access is required.")
    return user


def _validate_csrf(request: Request, conn, submitted: str) -> None:
    _validate_origin(request)
    raw_session = request.cookies.get(_COOKIE_NAME, "")
    if not auth.validate_csrf(conn, raw_session, submitted):
        raise HTTPException(status_code=403, detail="This form expired. Refresh and try again.")


def _csrf_for_session(request: Request, conn) -> str:
    raw_session = request.cookies.get(_COOKIE_NAME, "")
    row = conn.execute(
        "SELECT csrf_token FROM account_sessions WHERE token_hash = ?",
        (auth.hash_token(raw_session),),
    ).fetchone()
    return row["csrf_token"] if row else ""


@page_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if optional_user(request):
        return RedirectResponse("/account", status_code=302)
    return templates.TemplateResponse(request=request, name="login.html", context={})


@page_router.post("/login")
async def login(request: Request):
    _validate_origin(request)
    _check_rate_limit(request, action="login", limit=10, window_seconds=15 * 60)
    form = await request.form()
    conn = db.connect()
    try:
        try:
            record = db.get_login_record(conn, str(form.get("email", "")))
        except ValueError:
            record = None
        password = str(form.get("password", ""))
        if record is None or not auth.verify_password(password, record["password_hash"]):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"error": "Email or password was not recognized."},
                status_code=401,
            )
        session = auth.create_session(conn, int(record["id"]))
    finally:
        conn.close()
    response = RedirectResponse("/account", status_code=302)
    _set_session_cookie(response, session.token)
    return response


@page_router.post("/logout")
async def logout(request: Request):
    form = await request.form()
    conn = db.connect()
    try:
        _validate_csrf(request, conn, str(form.get("csrf_token", "")))
        auth.delete_session(conn, request.cookies.get(_COOKIE_NAME, ""))
    finally:
        conn.close()
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(_COOKIE_NAME, path="/", secure=_secure_cookie(), samesite="lax")
    return response


@page_router.get("", response_class=HTMLResponse)
async def account_dashboard(request: Request):
    conn = db.connect()
    try:
        user = _require_user(request, conn)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "user": user,
                "csrf_token": _csrf_for_session(request, conn),
                "taste_profile": db.get_profile(conn, user.id),
                "saved": db.saved_list(conn, user.id),
                "watched": db.watched_list(conn, user.id),
                "history": db.recommendation_history(conn, user.id),
                "provider_keys": credentials.provider_key_summaries(
                    conn,
                    user_id=user.id,
                ),
                "provider_options": credentials.PROVIDER_LABELS,
                "anilist_connection": db.anilist_connection_summary(
                    conn,
                    user_id=user.id,
                ),
                "anilist_available": bool(
                    os.getenv("OMAKASE_ANILIST_CLIENT_ID", "").strip() and _anilist_client_secret()
                ),
            },
        )
    finally:
        conn.close()


@page_router.get("/integrations/anilist/connect")
async def connect_anilist(request: Request):
    conn = db.connect()
    try:
        user = _require_user(request, conn)
        client_id = os.getenv("OMAKASE_ANILIST_CLIENT_ID", "").strip()
        if not client_id or not _anilist_client_secret():
            raise HTTPException(
                status_code=503,
                detail="AniList connection is not configured yet.",
            )
        state = anilist.generate_authorization_state()
        db.create_oauth_flow(
            conn,
            user_id=user.id,
            provider="anilist",
            state_hash=auth.hash_token(state),
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        )
        redirect_uri = f"{_public_url()}/account/integrations/anilist/callback"
        target = anilist.build_authorize_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
        )
        return RedirectResponse(target, status_code=302)
    finally:
        conn.close()


@page_router.get("/integrations/anilist/callback")
async def anilist_callback(request: Request):
    conn = db.connect()
    synced_count = 0
    try:
        user = _require_user(request, conn)
        code = request.query_params.get("code", "").strip()
        state = request.query_params.get("state", "").strip()
        if not code or not state:
            raise HTTPException(
                status_code=400,
                detail="AniList did not return a complete authorization.",
            )
        valid_flow = db.consume_oauth_flow(
            conn,
            user_id=user.id,
            provider="anilist",
            state_hash=auth.hash_token(state),
        )
        if not valid_flow:
            raise HTTPException(
                status_code=400,
                detail="AniList authorization is invalid or expired. Start again.",
            )
        client_id = os.getenv("OMAKASE_ANILIST_CLIENT_ID", "").strip()
        client_secret = _anilist_client_secret()
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=503,
                detail="AniList connection is not configured yet.",
            )
        redirect_uri = f"{_public_url()}/account/integrations/anilist/callback"
        try:
            token = anilist.exchange_code(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                code=code,
            )
            identity = anilist.viewer_identity(token)
            db.upsert_anilist_connection(
                conn,
                user_id=user.id,
                anilist_user_id=int(identity["id"]),
                anilist_username=str(identity["name"]),
                encrypted_access_token=credentials.encrypt_secret(token),
            )
            for pending in db.pending_anilist_watched(conn, user_id=user.id):
                sync_result = _sync_watched_to_anilist(
                    conn,
                    user_id=user.id,
                    recommendation_id=int(pending["id"]),
                    source_username=str(pending["source_username"]),
                    recommendation_url=pending["url"],
                    watched_score=int(pending["watched_score"]),
                    connected_username=str(identity["name"]),
                    access_token=token,
                )
                if sync_result["state"] == "synced":
                    synced_count += 1
        except (credentials.CredentialError, httpx.HTTPError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail="AniList did not complete the connection. Try again.",
            ) from exc
        return RedirectResponse(
            f"/account?anilist=connected&synced={synced_count}",
            status_code=302,
        )
    finally:
        conn.close()


@page_router.post("/integrations/anilist/disconnect")
async def disconnect_anilist(request: Request):
    form = await request.form()
    conn = db.connect()
    try:
        user = _require_user(request, conn)
        _validate_csrf(request, conn, str(form.get("csrf_token", "")))
        db.delete_anilist_connection(conn, user_id=user.id)
        return RedirectResponse("/account?anilist=disconnected", status_code=302)
    finally:
        conn.close()


@page_router.get("/admin/requests", response_class=HTMLResponse)
async def admin_requests(request: Request):
    conn = db.connect()
    try:
        user = _session_user(request, conn)
        if user is None:
            return RedirectResponse("/account/login", status_code=302)
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Owner access is required.")
        return templates.TemplateResponse(
            request=request,
            name="admin_requests.html",
            context={
                "user": user,
                "csrf_token": _csrf_for_session(request, conn),
                "accepted_invitations": db.list_accepted_invitations(conn),
                "requests": [
                    item for item in db.list_access_requests(conn) if item["status"] != "claimed"
                ],
                "focus": request.query_params.get("focus", ""),
            },
        )
    finally:
        conn.close()


@page_router.post("/admin/requests/{request_id}/approve")
async def approve_request(request_id: int, request: Request):
    form = await request.form()
    conn = db.connect()
    try:
        user = _require_admin(request, conn)
        _validate_csrf(request, conn, str(form.get("csrf_token", "")))
        try:
            token = db.approve_access_request(
                conn,
                request_id=request_id,
                admin_id=user.id,
            )
        except db.InviteError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()
    return {"invite_url": f"{_public_url()}/account/invite#{token}"}


@page_router.post("/admin/requests/{request_id}/decline")
async def decline_request(request_id: int, request: Request):
    form = await request.form()
    conn = db.connect()
    try:
        user = _require_admin(request, conn)
        _validate_csrf(request, conn, str(form.get("csrf_token", "")))
        db.decline_access_request(conn, request_id=request_id, admin_id=user.id)
    finally:
        conn.close()
    return JSONResponse({"ok": True})


@page_router.post("/admin/invites")
async def create_owner_invite(request: Request):
    form = await request.form()
    conn = db.connect()
    try:
        user = _require_admin(request, conn)
        _validate_csrf(request, conn, str(form.get("csrf_token", "")))
        _check_rate_limit(request, action="owner-invite", limit=20, window_seconds=60 * 60)
        token = db.create_direct_invite(conn, admin_id=user.id)
    finally:
        conn.close()
    return {"invite_url": f"{_public_url()}/account/invite#{token}"}


@page_router.get("/invite", response_class=HTMLResponse)
async def invite_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="invite.html",
        context={},
    )


@page_router.post("/invite/claim")
async def claim_invite(request: Request):
    _validate_origin(request)
    _check_rate_limit(request, action="claim-invite", limit=10, window_seconds=15 * 60)
    form = await request.form()
    token = str(form.get("token", ""))
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    display_name = str(form.get("display_name", "")).strip()
    error = ""
    if password != str(form.get("confirm_password", "")):
        error = "Passwords do not match."
    elif not display_name:
        error = "Tell us what name to use."
    if error:
        return templates.TemplateResponse(
            request=request,
            name="invite.html",
            context={
                "error": error,
                "token": token,
                "email": email,
                "display_name": display_name,
            },
            status_code=400,
        )

    conn = db.connect()
    try:
        try:
            user_id = db.claim_invite(
                conn,
                token=token,
                email=email,
                password=password,
                display_name=display_name,
            )
        except (db.InviteError, ValueError) as exc:
            return templates.TemplateResponse(
                request=request,
                name="invite.html",
                context={
                    "error": str(exc),
                    "token": token,
                    "email": email,
                    "display_name": display_name,
                },
                status_code=400,
            )
        session = auth.create_session(conn, user_id)
    finally:
        conn.close()
    response = RedirectResponse("/account", status_code=302)
    _set_session_cookie(response, session.token)
    return response


@api_router.get("/session")
async def account_session(request: Request):
    conn = db.connect()
    try:
        user = _session_user(request, conn)
        if user is None:
            return {"authenticated": False}
        return {
            "authenticated": True,
            "user_id": user.id,
            "display_name": user.display_name,
            "role": user.role,
            "csrf_token": _csrf_for_session(request, conn),
            "taste_profile": db.get_profile(conn, user.id),
            "provider_keys": credentials.provider_key_summaries(
                conn,
                user_id=user.id,
            ),
            "remembered_setup": db.get_remembered_setup(conn, user.id),
        }
    finally:
        conn.close()


@api_router.put("/provider-keys/{provider}")
async def save_provider_key(provider: str, payload: ProviderKeyInput, request: Request):
    conn = db.connect()
    try:
        user = _require_user(request, conn)
        _validate_csrf(request, conn, request.headers.get("X-CSRF-Token", ""))
        try:
            return credentials.save_provider_key(
                conn,
                user_id=user.id,
                provider=provider,
                plaintext_key=payload.provider_key.get_secret_value(),
            )
        except credentials.KeyringUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except credentials.CredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@api_router.delete("/provider-keys/{provider}")
async def forget_provider_key(provider: str, request: Request):
    conn = db.connect()
    try:
        user = _require_user(request, conn)
        _validate_csrf(request, conn, request.headers.get("X-CSRF-Token", ""))
        try:
            normalized = credentials.validate_provider(provider)
            credentials.forget_provider_key(
                conn,
                user_id=user.id,
                provider=normalized,
            )
        except credentials.CredentialError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return {"provider": normalized, "saved": False}


@api_router.post("/profile")
async def save_profile(request: Request):
    conn = db.connect()
    try:
        user = _require_user(request, conn)
        _validate_csrf(request, conn, request.headers.get("X-CSRF-Token", ""))
        body = await request.json()
        taste_profile = str(body.get("taste_profile", ""))
        db.update_profile(conn, user.id, taste_profile)
    finally:
        conn.close()
    return {"ok": True}


@api_router.post("/recommendations/{recommendation_id}/feedback")
async def recommendation_feedback(
    recommendation_id: int,
    payload: FeedbackInput,
    request: Request,
):
    tracker_sync = None
    conn = db.connect()
    try:
        user = _require_user(request, conn)
        _validate_csrf(request, conn, request.headers.get("X-CSRF-Token", ""))
        recommendation = db.recommendation_for_user(
            conn,
            user_id=user.id,
            recommendation_id=recommendation_id,
        )
        if payload.state != "watched" and payload.watched_score is not None:
            raise HTTPException(
                status_code=400,
                detail="A watched score is only valid for Already watched.",
            )
        try:
            db.set_recommendation_feedback(
                conn,
                user_id=user.id,
                recommendation_id=recommendation_id,
                state=payload.state,
                watched_score=payload.watched_score,
            )
        except db.OwnershipError as exc:
            raise HTTPException(status_code=404, detail="Recommendation not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if (
            payload.state == "watched"
            and recommendation is not None
            and recommendation["source"] == "anilist"
        ):
            connection = db.anilist_connection_record(conn, user_id=user.id)
            if connection is None:
                tracker_sync = {
                    "state": "connection_required",
                    "detail": "Connect AniList to add this title and score to your anime list.",
                    "connect_url": "/account/integrations/anilist/connect",
                }
            else:
                try:
                    token = credentials.decrypt_secret(str(connection["encrypted_access_token"]))
                except credentials.CredentialError:
                    token = None
                tracker_sync = _sync_watched_to_anilist(
                    conn,
                    user_id=user.id,
                    recommendation_id=recommendation_id,
                    source_username=str(recommendation["source_username"]),
                    recommendation_url=recommendation["url"],
                    watched_score=int(payload.watched_score),
                    connected_username=str(connection["anilist_username"]),
                    access_token=token,
                )
            if tracker_sync["state"] == "connection_required":
                db.set_recommendation_tracker_sync(
                    conn,
                    user_id=user.id,
                    recommendation_id=recommendation_id,
                    state=tracker_sync["state"],
                    detail=tracker_sync["detail"],
                )
    finally:
        conn.close()
    response = {
        "ok": True,
        "state": payload.state,
        "watched_score": payload.watched_score if payload.state == "watched" else None,
    }
    if tracker_sync is not None:
        response["tracker_sync"] = tracker_sync
    return response
