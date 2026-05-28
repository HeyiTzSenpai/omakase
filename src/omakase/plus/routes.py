"""Omakase Plus web routes.

Mounted at ``/plus`` via an ``APIRouter``.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from omakase.plus.anilist import (
    _pkce_state,
    add_to_planning,
    build_authorize_url,
    exchange_code,
    generate_pkce,
    with_valid_token,
)
from omakase.plus.auth import (
    create_session,
    hash_password,
    verify_password,
)
from omakase.plus.auth import (
    delete_session as _delete_session,
)
from omakase.plus.automation import trigger_request_after_plan
from omakase.plus.deps import get_db
from omakase.plus.middleware import require_user
from omakase.plus.secrets import delete_secret, read_secret, store_secret

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/plus")

# ── Rate limiting ───────────────────────────────────────────
# Simple in-memory: {ip: [timestamp, ...]}
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 5
_RATE_WINDOW = 60  # seconds


def _signup_allowed() -> bool:
    """Signup is only enabled in private mode or when an invite code is set."""
    private = os.getenv("OMAKASE_PLUS_PRIVATE", "false").lower() == "true"
    invite = bool(os.getenv("OMAKASE_PLUS_INVITE"))
    return private or invite


# ── Session cookie helpers ──────────────────────────────────

_SESSION_COOKIE = "omakase_session"
_SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        max_age=_SESSION_MAX_AGE,
        samesite="lax",
    )


def _delete_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_SESSION_COOKIE,
        httponly=True,
        samesite="lax",
    )


# ── Login ───────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db=Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    now = datetime.now(timezone.utc).timestamp()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]

    # Check rate limit before verifying credentials
    if len(_login_attempts[ip]) >= _RATE_LIMIT:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many login attempts. Try again later."},
            status_code=429,
        )

    _login_attempts[ip].append(now)

    row = db.execute(
        "SELECT id, password_hash FROM users WHERE email = ?",
        (email,),
    ).fetchone()

    if row is None or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid email or password."},
        )

    session_id = create_session(db, row["id"])
    response = RedirectResponse(url="/plus/dashboard", status_code=302)
    _set_session_cookie(response, session_id)
    return response


# ── Signup ──────────────────────────────────────────────────


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: str = ""):
    if not _signup_allowed():
        return RedirectResponse(url="/plus/login", status_code=302)
    return templates.TemplateResponse(request, "signup.html", {"error": error})


@router.post("/signup", response_class=HTMLResponse)
async def signup_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db=Depends(get_db),
):
    if not _signup_allowed():
        return RedirectResponse(url="/plus/login", status_code=302)

    if not email or not password:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "Email and password are required."},
        )

    if password != confirm_password:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "Passwords do not match."},
        )

    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "Email already registered."},
        )

    password_hash = hash_password(password)
    cursor = db.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, password_hash),
    )
    user_id = cursor.lastrowid
    # Create an empty taste profile row
    db.execute(
        "INSERT INTO taste_profiles (user_id, content) VALUES (?, ?)",
        (user_id, ""),
    )
    db.commit()

    session_id = create_session(db, user_id)
    response = RedirectResponse(url="/plus/dashboard", status_code=302)
    _set_session_cookie(response, session_id)
    return response


# ── Logout ──────────────────────────────────────────────────


@router.post("/logout")
async def logout(request: Request, db=Depends(get_db)):
    session_id = request.cookies.get(_SESSION_COOKIE)
    if session_id:
        _delete_session(db, session_id)
    response = RedirectResponse(url="/", status_code=302)
    _delete_session_cookie(response)
    return response


# ── Dashboard (placeholder) ─────────────────────────────────


@router.get("/dashboard")
async def dashboard(user=Depends(require_user)):
    return HTMLResponse("Dashboard (coming in Phase 5)")


# ── Settings (secrets management) ───────────────────────────

_SECRET_KEYS = {
    "llm_api_key": "LLM API Key — your OpenAI / Anthropic / Gemini / DeepSeek key",
    "anilist_oauth_token": "AniList OAuth token (set up in Phase 3)",
    "overseerr_api_key": "Overseerr API key",
    "overseerr_url": "Overseerr URL (e.g. http://overseerr.lab:5055)",
}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request, saved: str = "", user=Depends(require_user), db=Depends(get_db)
):
    stored = {}
    for key_name in _SECRET_KEYS:
        val = read_secret(db, user.id, key_name)
        stored[key_name] = "••••••••" if val else ""
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"email": user.email, "keys": _SECRET_KEYS, "stored": stored, "saved": saved},
    )


@router.post("/settings", response_class=HTMLResponse)
async def settings_post(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_user),
):
    form = await request.form()
    user_id = user.id
    saved_keys: list[str] = []

    for key_name in _SECRET_KEYS:
        value = form.get(key_name, "").strip()
        if value and value != "••••••••":
            store_secret(db, user_id, key_name, value)
            saved_keys.append(key_name)
        elif form.get(f"delete_{key_name}", ""):
            delete_secret(db, user_id, key_name)

    msg = f"Saved: {', '.join(saved_keys)}" if saved_keys else "No changes made."
    return RedirectResponse(url=f"/plus/settings?saved={msg}", status_code=302)


# ── AniList OAuth integration ──────────────────────────────


@router.get("/integrations/anilist/connect")
async def anilist_connect(user=Depends(require_user)):
    """Initiate AniList OAuth flow (Authorization Code with PKCE)."""
    client_id = os.getenv("ANILIST_CLIENT_ID", "")
    if not client_id:
        return HTMLResponse("ANILIST_CLIENT_ID not configured.", status_code=500)

    code_verifier, code_challenge = generate_pkce()
    _pkce_state[user.id] = (code_verifier, code_challenge)

    base_url = os.getenv("OMAKASE_PLUS_URL", "http://localhost:8765")
    redirect_uri = f"{base_url}/plus/integrations/anilist/callback"

    authorize_url = build_authorize_url(client_id, redirect_uri, code_challenge)
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/integrations/anilist/callback")
async def anilist_callback(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_user),
):
    """Handle the AniList OAuth redirect and store the access token."""
    code = request.query_params.get("code")
    if not code:
        return HTMLResponse("Missing authorization code.", status_code=400)

    pkce_data = _pkce_state.pop(user.id, None)
    if pkce_data is None:
        return HTMLResponse(
            "No PKCE state found. Please start the connect flow again.",
            status_code=400,
        )
    code_verifier, _code_challenge = pkce_data

    client_id = os.getenv("ANILIST_CLIENT_ID", "")
    client_secret = os.getenv("ANILIST_CLIENT_SECRET", "")
    base_url = os.getenv("OMAKASE_PLUS_URL", "http://localhost:8765")
    redirect_uri = f"{base_url}/plus/integrations/anilist/callback"

    if not client_id or not client_secret:
        return HTMLResponse("AniList credentials not configured.", status_code=500)

    try:
        token = exchange_code(client_id, client_secret, redirect_uri, code, code_verifier)
    except Exception as e:
        return HTMLResponse(f"Token exchange failed: {e}", status_code=502)

    store_secret(db, user.id, "anilist_oauth_token", token)
    return RedirectResponse(url="/plus/settings", status_code=302)


@router.post("/integrations/anilist/disconnect")
async def anilist_disconnect(db=Depends(get_db), user=Depends(require_user)):
    """Remove the stored AniList OAuth token."""
    delete_secret(db, user.id, "anilist_oauth_token")
    return RedirectResponse(url="/plus/settings", status_code=302)


@router.post("/api/plan")
async def plan_api(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_user),
):
    """Add an anime to the user's AniList Planning list (JSON endpoint).

    Accepts ``{"anilist_id": 123, "title": "...", "status": "PLANNING"}``.
    Deduplicates against the local ``anilist_plannings`` table.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid JSON body"}

    anilist_id = body.get("anilist_id")
    title = body.get("title", "")
    status_val = body.get("status", "PLANNING")

    if not anilist_id or not title:
        return {"status": "error", "detail": "anilist_id and title are required"}

    # Local dedupe
    existing = db.execute(
        "SELECT id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()
    if existing:
        return {"status": "already_planned"}

    client_id = os.getenv("ANILIST_CLIENT_ID", "")
    client_secret = os.getenv("ANILIST_CLIENT_SECRET", "")
    base_url = os.getenv("OMAKASE_PLUS_URL", "http://localhost:8765")
    redirect_uri = f"{base_url}/plus/integrations/anilist/callback"

    try:
        with with_valid_token(db, user.id, client_id, client_secret, redirect_uri) as token:
            _anilist_result = add_to_planning(token, anilist_id, status_val)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    except httpx.HTTPError as e:
        return {"status": "error", "detail": f"AniList API error: {e}"}

    db.execute(
        "INSERT INTO anilist_plannings (user_id, anilist_id, title, status) VALUES (?, ?, ?, ?)",
        (user.id, anilist_id, title, status_val),
    )
    db.commit()

    return {"status": "ok"}


# ── Phase 4: Overseerr auto-request ─────────────────────────


@router.post("/api/auto-request")
async def auto_request(
    request: Request,
    user=Depends(require_user),
    db=Depends(get_db),
):
    """Auto-request an anime via Overseerr based on AniList planning data.

    Body: {"anilist_id": 123, "title": "Anime Title"}
    """
    body = await request.json()
    anilist_id = body["anilist_id"]
    title = body["title"]

    # Upsert into anilist_plannings to get the planning primary key
    row = db.execute(
        "SELECT id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()

    if row:
        planning_id = row["id"]
        db.execute(
            "UPDATE anilist_plannings SET title = ? WHERE id = ?",
            (title, planning_id),
        )
    else:
        cursor = db.execute(
            "INSERT INTO anilist_plannings (user_id, anilist_id, title) VALUES (?, ?, ?)",
            (user.id, anilist_id, title),
        )
        planning_id = cursor.lastrowid
    db.commit()

    # Trigger Overseerr request
    status = trigger_request_after_plan(db, user.id, planning_id, title)

    if status == "requested":
        req_row = db.execute(
            """SELECT overseerr_request_id FROM overseerr_requests
               WHERE user_id = ? AND anilist_planning_id = ?
               ORDER BY id DESC LIMIT 1""",
            (user.id, planning_id),
        ).fetchone()
        return {
            "status": "requested",
            "overseerr_request_id": req_row["overseerr_request_id"] if req_row else None,
        }
    elif status == "not_found":
        return {"status": "not_found"}
    else:
        return {"status": "error", "detail": "Failed to submit Overseerr request"}


@router.get("/integrations/overseerr/status")
async def overseerr_status(
    user=Depends(require_user),
    db=Depends(get_db),
):
    """Return recent Overseerr request statuses for the logged-in user."""
    rows = db.execute(
        """SELECT orr.id, orr.anilist_planning_id, orr.overseerr_request_id,
                  orr.status, orr.created_at, ap.anilist_id, ap.title
           FROM overseerr_requests orr
           LEFT JOIN anilist_plannings ap ON ap.id = orr.anilist_planning_id
           WHERE orr.user_id = ?
           ORDER BY orr.created_at DESC
           LIMIT 50""",
        (user.id,),
    ).fetchall()

    return [
        {
            "id": r["id"],
            "anilist_planning_id": r["anilist_planning_id"],
            "overseerr_request_id": r["overseerr_request_id"],
            "status": r["status"],
            "created_at": r["created_at"],
            "anilist_id": r["anilist_id"],
            "title": r["title"],
        }
        for r in rows
    ]
