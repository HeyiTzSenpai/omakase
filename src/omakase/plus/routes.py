"""Omakase Plus web routes.

Mounted at ``/plus`` via an ``APIRouter``.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from omakase.plus.auth import (
    create_session,
    hash_password,
    verify_password,
)
from omakase.plus.auth import (
    delete_session as _delete_session,
)
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

    existing = db.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
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
async def settings_page(request: Request, saved: str = "", user=Depends(require_user), db=Depends(get_db)):
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
