"""Omakase Plus web routes.

Mounted at ``/plus`` via an ``APIRouter``.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from omakase.engine import run as run_pipeline
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
from omakase.plus.deps import get_db
from omakase.plus.middleware import require_user
from omakase.plus.secrets import delete_secret, read_secret, store_secret
from omakase.types import OmakaseConfig, resolve_model_preset

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


# ── Dashboard ───────────────────────────────────────────────

_ANILIST_ID_RE = re.compile(r"https://anilist\.co/anime/(\d+)/")


def _extract_anilist_id(url: str | None) -> int | None:
    """Extract AniList media ID from a recommendation URL, if possible."""
    if not url:
        return None
    m = _ANILIST_ID_RE.search(url)
    return int(m.group(1)) if m else None


@router.get("/dashboard")
async def dashboard(
    request: Request,
    run: str = "",
    error: str = "",
    user=Depends(require_user),
    db=Depends(get_db),
):
    # Load taste profile
    tp = db.execute(
        "SELECT content, updated_at FROM taste_profiles WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    taste_profile_content = tp["content"] if tp else ""
    taste_profile_updated = tp["updated_at"] if tp else ""

    # Load recent runs (last 10)
    recent_rows = db.execute(
        """SELECT id, source, model, picks, created_at
           FROM run_history WHERE user_id = ?
           ORDER BY id DESC LIMIT 10""",
        (user.id,),
    ).fetchall()
    runs = []
    for r in recent_rows:
        try:
            picks = json.loads(r["picks"])
        except (json.JSONDecodeError, TypeError):
            picks = []
        runs.append({
            "id": r["id"],
            "source": r["source"],
            "model": r["model"],
            "picks": picks,
            "created_at": r["created_at"],
        })

    # Load current-run picks (if ?run=N)
    current_run_picks: list[dict] = []
    current_run_id = 0
    current_run_source = ""
    if run:
        try:
            run_row = db.execute(
                "SELECT id, source, model, picks FROM run_history WHERE id = ? AND user_id = ?",
                (int(run), user.id),
            ).fetchone()
            if run_row:
                current_run_id = run_row["id"]
                current_run_source = run_row["source"]
                picks_data = json.loads(run_row["picks"])
                for p in picks_data:
                    anilist_id = _extract_anilist_id(p.get("url"))
                    p["anilist_id"] = anilist_id
                current_run_picks = picks_data
        except (ValueError, json.JSONDecodeError, TypeError):
            pass

    # Load planning queue
    planning_rows = db.execute(
        """SELECT id, anilist_id, title, added_at, status as planning_status
           FROM anilist_plannings
           WHERE user_id = ?
           ORDER BY added_at DESC
           LIMIT 50""",
        (user.id,),
    ).fetchall()
    plannings = []
    planned_ids: set[int] = set()
    for p in planning_rows:
        a_id = p["anilist_id"]
        planned_ids.add(a_id)
        plannings.append({
            "id": p["id"],
            "anilist_id": a_id,
            "title": p["title"],
            "added_at": p["added_at"],
            "planning_status": p["planning_status"],
        })

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "email": user.email,
            "taste_profile": taste_profile_content,
            "taste_profile_updated": taste_profile_updated,
            "runs": runs,
            "current_run_picks": current_run_picks,
            "current_run_id": current_run_id,
            "current_run_source": current_run_source,
            "plannings": plannings,
            "planned_ids": planned_ids,
            "error": error,
        },
    )


@router.post("/dashboard/profile")
async def dashboard_profile(
    db=Depends(get_db),
    user=Depends(require_user),
    profile: str = Form(""),
):
    existing = db.execute(
        "SELECT id FROM taste_profiles WHERE user_id = ?", (user.id,)
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE taste_profiles SET content = ?, updated_at = datetime('now') WHERE id = ?",
            (profile, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO taste_profiles (user_id, content, updated_at) VALUES (?, ?, datetime('now'))",
            (user.id, profile),
        )
    db.commit()
    return RedirectResponse(url="/plus/dashboard", status_code=302)


@router.post("/dashboard/run")
async def dashboard_run(
    db=Depends(get_db),
    user=Depends(require_user),
    source: str = Form("anilist"),
    mode: str = Form("fast"),
    count: int = Form(8),
    temperature: float = Form(0.4),
    username: str = Form(""),
    model: str = Form(""),
    use_planning: bool = Form(False),
    skip_profile: bool = Form(False),
):
    # Read taste profile from DB
    tp = db.execute(
        "SELECT content FROM taste_profiles WHERE user_id = ?", (user.id,)
    ).fetchone()
    taste_profile_content = tp["content"] if tp else ""

    # Read LLM API key and preferred backend from stored secrets
    api_key = read_secret(db, user.id, "llm_api_key")
    llm_type = read_secret(db, user.id, "llm_backend") or ""
    if not llm_type:
        llm_type = "openai" if api_key else "ollama"

    # Stage env vars for downstream clients
    if api_key:
        os.environ["OMAKASE_API_KEY"] = api_key

    default_model = "qwen2.5:7b" if mode == "fast" else "qwen2.5:14b"
    llm_url, llm_type_resolved, model_resolved, supports_json = resolve_model_preset(
        "http://localhost:11434",
        llm_type,
        model if model else default_model,
        mode,
    )

    # Write taste profile to a temp file so the engine can read it
    import tempfile

    profile_path = ""
    if taste_profile_content and not skip_profile:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        tmp.write(taste_profile_content)
        tmp.close()
        profile_path = tmp.name

    cfg = OmakaseConfig(
        source=source,
        username=username.strip() or user.email.split("@")[0],
        llm_url=llm_url,
        model=model_resolved,
        profile_path=profile_path,
        candidate_pool_size=count,
        temperature=temperature,
        llm_type=llm_type_resolved,
        mode=mode,
        supports_json_mode=supports_json,
        use_planning=use_planning,
    )

    try:
        recs = run_pipeline(cfg)
    except Exception as e:
        if profile_path:
            try:
                os.unlink(profile_path)
            except OSError:
                pass
        import urllib.parse

        msg = urllib.parse.quote(f"Recommendation failed: {e}")
        return RedirectResponse(
            url=f"/plus/dashboard?error={msg}",
            status_code=302,
        )

    # Clean up temp file
    if profile_path:
        try:
            os.unlink(profile_path)
        except OSError:
            pass

    picks_json = json.dumps([asdict(r) for r in recs])
    cursor = db.execute(
        "INSERT INTO run_history (user_id, source, model, picks) VALUES (?, ?, ?, ?)",
        (user.id, source, model_resolved, picks_json),
    )
    db.commit()
    run_id = cursor.lastrowid

    return RedirectResponse(url=f"/plus/dashboard?run={run_id}", status_code=302)


@router.post("/dashboard/plan")
async def dashboard_plan(
    db=Depends(get_db),
    user=Depends(require_user),
    anilist_id: int = Form(...),
    title: str = Form(""),
):
    # Check if already planned locally
    existing = db.execute(
        "SELECT id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()
    if existing:
        return RedirectResponse(url="/plus/dashboard", status_code=302)

    # Try to add to AniList Planning via OAuth (best-effort)
    client_id = os.getenv("ANILIST_CLIENT_ID", "")
    client_secret = os.getenv("ANILIST_CLIENT_SECRET", "")
    base_url = os.getenv("OMAKASE_PLUS_URL", "http://localhost:8765")
    redirect_uri = f"{base_url}/plus/integrations/anilist/callback"
    try:
        with with_valid_token(db, user.id, client_id, client_secret, redirect_uri) as token:
            add_to_planning(token, anilist_id, "PLANNING")
    except (ValueError, httpx.HTTPError):
        pass

    # Always insert locally
    db.execute(
        "INSERT INTO anilist_plannings (user_id, anilist_id, title, status) VALUES (?, ?, ?, ?)",
        (user.id, anilist_id, title, "PLANNING"),
    )
    db.commit()

    return RedirectResponse(url="/plus/dashboard", status_code=302)


@router.post("/dashboard/plan-and-download")
async def dashboard_plan_and_download(
    db=Depends(get_db),
    user=Depends(require_user),
    anilist_id: int = Form(...),
    title: str = Form(""),
):
    """Plan on AniList + search nyaa.si + add to Real-Debrid. Form-based route."""
    msg = ""

    # 1. Plan on AniList (best-effort)
    existing = db.execute(
        "SELECT id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()
    if not existing:
        client_id = os.getenv("ANILIST_CLIENT_ID", "")
        client_secret = os.getenv("ANILIST_CLIENT_SECRET", "")
        base_url = os.getenv("OMAKASE_PLUS_URL", "http://localhost:8765")
        redirect_uri = f"{base_url}/plus/integrations/anilist/callback"
        try:
            with with_valid_token(db, user.id, client_id, client_secret, redirect_uri) as token:
                add_to_planning(token, anilist_id, "PLANNING")
        except (ValueError, httpx.HTTPError):
            pass
        db.execute(
            "INSERT INTO anilist_plannings (user_id, anilist_id, title, status) VALUES (?, ?, ?, ?)",
            (user.id, anilist_id, title, "PLANNING"),
        )
        db.commit()
        msg = "Planned"

    # 2. Search nyaa + add to Real-Debrid
    from omakase.plus.automation import search_and_download

    result = await search_and_download(db, user.id, title)

    if result["status"] == "ok":
        msg += f" · Downloading: {result.get('torrent_title', title)} ({result.get('size', '?')}, {result.get('seeders', 0)} seeds)"
    elif result["status"] == "no_rd_key":
        msg += " · Set Real-Debrid API key in Settings to auto-download"
    elif result["status"] == "not_found":
        msg += f" · No torrents found on nyaa.si for \"{title}\""
    elif result["status"] == "rd_error":
        msg += f" · Real-Debrid: {result.get('detail', 'error')}"

    return RedirectResponse(url=f"/plus/dashboard?error={msg}", status_code=302)


# ── Settings (secrets management) ───────────────────────────

_SECRET_KEYS = {
    "llm_api_key": "LLM API Key — your OpenAI / Anthropic / Gemini / DeepSeek key",
    "llm_backend": "LLM Backend — openai, anthropic, gemini, deepseek, openrouter, groq, together",
    "anilist_oauth_token": "AniList OAuth token (set up in Phase 3)",
    "realdebrid_api_key": "Real-Debrid API key (from https://real-debrid.com/apitoken)",
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


# ── Plan & Download (Nyaa + Real-Debrid) ─────────────────────


@router.post("/api/plan-and-download")
async def plan_and_download(
    request: Request,
    user=Depends(require_user),
    db=Depends(get_db),
):
    """Plan an anime on AniList, then search nyaa.si and add to Real-Debrid.

    Body: {"anilist_id": 123, "title": "Anime Title"}
    """
    body = await request.json()
    anilist_id = body.get("anilist_id")
    title = body.get("title", "")

    if not anilist_id or not title:
        return {"status": "error", "detail": "anilist_id and title are required"}

    # 1. Add to AniList Planning if not already planned
    existing = db.execute(
        "SELECT id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()

    if not existing:
        client_id = os.getenv("ANILIST_CLIENT_ID", "")
        client_secret = os.getenv("ANILIST_CLIENT_SECRET", "")
        base_url = os.getenv("OMAKASE_PLUS_URL", "http://localhost:8765")
        redirect_uri = f"{base_url}/plus/integrations/anilist/callback"
        try:
            with with_valid_token(db, user.id, client_id, client_secret, redirect_uri) as token:
                add_to_planning(token, anilist_id, "PLANNING")
        except (ValueError, httpx.HTTPError):
            pass

        db.execute(
            "INSERT INTO anilist_plannings (user_id, anilist_id, title, status) VALUES (?, ?, ?, ?)",
            (user.id, anilist_id, title, "PLANNING"),
        )
        db.commit()

    # 2. Search nyaa.si for the best torrent
    from omakase.plus.automation import search_and_download

    result = await search_and_download(db, user.id, title)
    return result
