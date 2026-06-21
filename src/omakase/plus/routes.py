"""Omakase Plus web routes.

Mounted at ``/plus`` via an ``APIRouter``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
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
from omakase.plus.feedback import feedback_for_prompt, save_feedback
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

# ── Background recommendation jobs ──────────────────────────
# A run can take 1-2 minutes (Pro mode / reasoning models). Calling the
# synchronous run_pipeline inline both exceeds the reverse-proxy read timeout
# (504s) and blocks uvicorn's single event loop for the whole run. So
# POST /api/run starts the run in a daemon thread and returns a job_id at once;
# the client polls /api/run/status/{job_id}. The container runs a single
# uvicorn worker, so an in-memory registry needs no cross-process coordination.
# The worker is pure compute — the run_history write happens later in the
# status endpoint's request-scoped connection, never across threads.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _set_job(job_id: str, **fields: object) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)


def _run_job(job_id: str, cfg: OmakaseConfig, profile_path: str) -> None:
    """Run the pipeline off the event loop; publish recs or error to the registry.

    Writes the payload before flipping ``status`` to its terminal value so a
    reader that sees a terminal status always sees a complete result.
    """
    try:
        recs = run_pipeline(cfg)
    except Exception as e:
        _set_job(job_id, detail=str(e), status="error")
    else:
        _set_job(job_id, recs=recs, status="completed")
    finally:
        if profile_path:
            try:
                os.unlink(profile_path)
            except OSError:
                pass


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
_LANES = {"best_match", "new_seasons", "hidden_gems", "plan_list"}


def _extract_anilist_id(url: str | None) -> int | None:
    """Extract AniList media ID from a recommendation URL, if possible."""
    if not url:
        return None
    m = _ANILIST_ID_RE.search(url)
    return int(m.group(1)) if m else None


def _positive_int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _recommendation_anilist_id(pick: dict, source: str) -> int | None:
    stored_id = _positive_int_or_none(pick.get("anilist_id"))
    if stored_id is not None:
        return stored_id

    if (pick.get("source") or source) == "anilist":
        media_id = _positive_int_or_none(pick.get("media_id"))
        if media_id is not None:
            return media_id

    return _extract_anilist_id(pick.get("url"))


def _normalize_lane(value: str | None) -> str:
    return value if isinstance(value, str) and value in _LANES else "best_match"


def _coerce_optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{field_name} must be an integer") from None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


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

    anilist_username = read_secret(db, user.id, "anilist_username") or ""

    # Load recent runs (last 10)
    recent_rows = db.execute(
        """SELECT id, source, model, picks, lane, created_at
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
        runs.append(
            {
                "id": r["id"],
                "source": r["source"],
                "model": r["model"],
                "lane": r["lane"],
                "picks": picks,
                "created_at": r["created_at"],
            }
        )

    # Load current-run picks (if ?run=N)
    current_run_picks: list[dict] = []
    current_run_id = 0
    current_run_source = ""
    current_run_lane = "best_match"
    if run:
        try:
            run_row = db.execute(
                "SELECT id, source, model, picks, lane FROM run_history WHERE id = ? AND user_id = ?",
                (int(run), user.id),
            ).fetchone()
            if run_row:
                current_run_id = run_row["id"]
                current_run_source = run_row["source"]
                current_run_lane = _normalize_lane(
                    run_row["lane"] if "lane" in run_row.keys() else "best_match"
                )
                picks_data = json.loads(run_row["picks"])
                for p in picks_data:
                    if not isinstance(p, dict):
                        continue
                    anilist_id = _recommendation_anilist_id(p, current_run_source)
                    p["anilist_id"] = anilist_id
                    p["media_id"] = _positive_int_or_none(p.get("media_id")) or anilist_id
                current_run_picks = picks_data
        except (ValueError, json.JSONDecodeError, TypeError):
            pass

    # Load planning queue
    planning_rows = db.execute(
        """SELECT id, anilist_id, title, added_at, status as planning_status,
                  download_status, download_info, rd_torrent_id
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
        plannings.append(
            {
                "id": p["id"],
                "anilist_id": a_id,
                "title": p["title"],
                "added_at": p["added_at"],
                "planning_status": p["planning_status"],
                "download_status": p["download_status"] or "",
                "download_info": p["download_info"] or "",
                "rd_torrent_id": p["rd_torrent_id"] or "",
            }
        )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "email": user.email,
            "taste_profile": taste_profile_content,
            "taste_profile_updated": taste_profile_updated,
            "anilist_username": anilist_username,
            "runs": runs,
            "current_run_picks": current_run_picks,
            "current_run_id": current_run_id,
            "current_run_source": current_run_source,
            "current_run_lane": current_run_lane,
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
    existing = db.execute("SELECT id FROM taste_profiles WHERE user_id = ?", (user.id,)).fetchone()
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


@router.post("/api/run")
async def api_run(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_user),
):
    """Start a recommendation run in the background and return a job id.

    The run executes in a daemon thread so this request returns in
    milliseconds; the client polls ``/plus/api/run/status/{job_id}``. This
    decouples the multi-minute LLM run from any single request's lifetime —
    which previously tripped the reverse-proxy read timeout — and keeps the
    event loop free to serve health checks and polls.
    """
    body = await request.json()
    source = body.get("source", "anilist")
    username = body.get("username", "")
    mode = body.get("mode", "fast")
    count = body.get("count", 8)
    temperature = body.get("temperature", 0.4)
    model = body.get("model", "")
    use_planning = body.get("use_planning", False)
    skip_profile = body.get("skip_profile", False)
    lane = _normalize_lane(body.get("lane"))
    if lane == "plan_list":
        use_planning = True

    tp = db.execute("SELECT content FROM taste_profiles WHERE user_id = ?", (user.id,)).fetchone()
    taste_profile_content = tp["content"] if tp else ""

    api_key = read_secret(db, user.id, "llm_api_key")
    llm_type = read_secret(db, user.id, "llm_backend") or ""
    if not llm_type:
        llm_type = "openai" if api_key else "ollama"

    if api_key:
        os.environ["OMAKASE_API_KEY"] = api_key

    default_model = "qwen2.5:7b" if mode == "fast" else "qwen2.5:14b"
    llm_url, llm_type_resolved, model_resolved, supports_json = resolve_model_preset(
        "http://localhost:11434",
        llm_type,
        model if model else default_model,
        mode,
    )

    profile_path = ""
    if taste_profile_content and not skip_profile:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        tmp.write(taste_profile_content)
        tmp.close()
        profile_path = tmp.name

    default_username = read_secret(db, user.id, "anilist_username") or ""
    feedback = feedback_for_prompt(db, user.id)
    cfg = OmakaseConfig(
        source=source,
        username=username.strip() or default_username or user.email.split("@")[0],
        llm_url=llm_url,
        model=model_resolved,
        profile_path=profile_path,
        # Over-fetch: the genre-targeted pool is dominated by sequels of shows
        # already in the user's history, which the franchise filter then drops.
        # Fetching only `count` would leave ~1 survivor. Fetch a generous pool
        # so enough non-franchise candidates remain for `count` picks.
        candidate_pool_size=min(300, max(count * 12, 120)),
        num_recommendations=count,
        temperature=temperature,
        llm_type=llm_type_resolved,
        mode=mode,
        supports_json_mode=supports_json,
        use_planning=use_planning,
        recommendation_lane=lane,
        feedback=feedback,
    )

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "user_id": user.id,
            "source": source,
            "model": model_resolved,
            "lane": lane,
            "recs": None,
            "detail": "",
            "run_id": None,
        }
    threading.Thread(target=_run_job, args=(job_id, cfg, profile_path), daemon=True).start()

    return {"status": "running", "job_id": job_id}


@router.get("/api/run/status/{job_id}")
async def api_run_status(
    job_id: str,
    db=Depends(get_db),
    user=Depends(require_user),
):
    """Poll a background recommendation job.

    Returns ``running`` while in flight. On first completion, persists the run
    to ``run_history`` via this request's connection and returns ``ok`` with the
    run id + recommendations. Failed or unknown/expired jobs return ``error``.
    The persist runs on the event loop with no ``await`` between the status read
    and the write, so concurrent polls cannot double-insert under one worker.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None or job["user_id"] != user.id:
            return {"status": "error", "detail": "Unknown or expired job."}
        status = job["status"]
        if status == "running":
            return {"status": "running"}
        if status == "error":
            detail = job["detail"]
            _jobs.pop(job_id, None)
            return {"status": "error", "detail": detail}
        # status == "completed": persist once, then report ok.
        recs = job["recs"]
        source = job["source"]
        model = job["model"]
        lane = job["lane"]

    picks_json = json.dumps([asdict(r) for r in recs])
    cursor = db.execute(
        "INSERT INTO run_history (user_id, source, model, picks, lane) VALUES (?, ?, ?, ?, ?)",
        (user.id, source, model, picks_json, lane),
    )
    db.commit()
    run_id = cursor.lastrowid

    with _jobs_lock:
        _jobs.pop(job_id, None)

    return {
        "status": "ok",
        "run_id": run_id,
        "source": source,
        "model": model,
        "lane": lane,
        "recommendations": [asdict(r) for r in recs],
    }


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
    lane: str = Form("best_match"),
):
    lane = _normalize_lane(lane)
    if lane == "plan_list":
        use_planning = True

    # Read taste profile from DB
    tp = db.execute("SELECT content FROM taste_profiles WHERE user_id = ?", (user.id,)).fetchone()
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

    default_username = read_secret(db, user.id, "anilist_username") or ""
    feedback = feedback_for_prompt(db, user.id)
    cfg = OmakaseConfig(
        source=source,
        username=username.strip() or default_username or user.email.split("@")[0],
        llm_url=llm_url,
        model=model_resolved,
        profile_path=profile_path,
        candidate_pool_size=300,
        num_recommendations=count,
        temperature=temperature,
        llm_type=llm_type_resolved,
        mode=mode,
        supports_json_mode=supports_json,
        use_planning=use_planning,
        recommendation_lane=lane,
        feedback=feedback,
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
        "INSERT INTO run_history (user_id, source, model, picks, lane) VALUES (?, ?, ?, ?, ?)",
        (user.id, source, model_resolved, picks_json, lane),
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


@router.post("/dashboard/unplan")
async def dashboard_unplan(
    db=Depends(get_db),
    user=Depends(require_user),
    anilist_id: int = Form(...),
):
    """Remove an anime from the local planning list."""
    db.execute(
        "DELETE FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    )
    db.commit()
    return RedirectResponse(url="/plus/dashboard", status_code=302)


@router.post("/dashboard/download")
async def dashboard_download(
    db=Depends(get_db),
    user=Depends(require_user),
    anilist_id: int = Form(...),
    title: str = Form(""),
):
    """Create a local planning target, then search nyaa.si and add to Real-Debrid."""
    messages: list[str] = []

    existing = db.execute(
        "SELECT id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO anilist_plannings (user_id, anilist_id, title, status) VALUES (?, ?, ?, ?)",
            (user.id, anilist_id, title, "PLANNING"),
        )
        db.commit()
        messages.append("Queued")

    from omakase.plus.automation import search_and_download

    result = await search_and_download(db, user.id, title)

    if result["status"] == "ok":
        rd_id = result.get("rd_id", "")
        info = f"{result.get('torrent_title', title)} ({result.get('size', '?')}, {result.get('seeders', 0)}s)"
        messages.append(f"Downloading: {info}")
        db.execute(
            "UPDATE anilist_plannings SET download_status = ?, download_info = ?, rd_torrent_id = ? WHERE user_id = ? AND anilist_id = ?",
            ("requested", info, rd_id, user.id, anilist_id),
        )
        db.commit()
    elif result["status"] == "no_rd_key":
        messages.append("Set Real-Debrid API key in Settings to auto-download")
        db.execute(
            "UPDATE anilist_plannings SET download_status = ? WHERE user_id = ? AND anilist_id = ?",
            ("no_rd_key", user.id, anilist_id),
        )
        db.commit()
    elif result["status"] == "not_found":
        messages.append(f'No torrents found on nyaa.si for "{title}"')
        db.execute(
            "UPDATE anilist_plannings SET download_status = ? WHERE user_id = ? AND anilist_id = ?",
            ("not_found", user.id, anilist_id),
        )
        db.commit()
    elif result["status"] == "rd_error":
        messages.append(f"Real-Debrid: {result.get('detail', 'error')}")
        db.execute(
            "UPDATE anilist_plannings SET download_status = ? WHERE user_id = ? AND anilist_id = ?",
            ("error", user.id, anilist_id),
        )
        db.commit()

    msg = " · ".join(messages)
    return RedirectResponse(url=f"/plus/dashboard?error={msg}", status_code=302)


@router.post("/dashboard/remove-plan")
async def dashboard_remove_plan(
    db=Depends(get_db),
    user=Depends(require_user),
    anilist_id: int = Form(...),
):
    """Remove an anime from AniList Planning and the local database."""
    title = ""

    # Remove from AniList (best-effort)
    client_id = os.getenv("ANILIST_CLIENT_ID", "")
    client_secret = os.getenv("ANILIST_CLIENT_SECRET", "")
    base_url = os.getenv("OMAKASE_PLUS_URL", "http://localhost:8765")
    redirect_uri = f"{base_url}/plus/integrations/anilist/callback"
    try:
        with with_valid_token(db, user.id, client_id, client_secret, redirect_uri) as token:
            from omakase.plus.anilist import remove_from_planning

            remove_from_planning(token, anilist_id)
    except (ValueError, httpx.HTTPError):
        pass

    # Remove from local database
    row = db.execute(
        "SELECT title FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()
    if row:
        title = row["title"]
        db.execute(
            "DELETE FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
            (user.id, anilist_id),
        )
        db.commit()

    msg = f'Removed "{title}" from planning list' if title else "Removed from planning list"
    return RedirectResponse(url=f"/plus/dashboard?error={msg}", status_code=302)


@router.post("/dashboard/remove-from-rd")
async def dashboard_remove_from_rd(
    db=Depends(get_db),
    user=Depends(require_user),
    anilist_id: int = Form(...),
):
    """Remove a torrent from Real-Debrid by its stored torrent ID."""
    row = db.execute(
        "SELECT title, rd_torrent_id FROM anilist_plannings WHERE user_id = ? AND anilist_id = ?",
        (user.id, anilist_id),
    ).fetchone()

    if not row or not row["rd_torrent_id"]:
        return RedirectResponse(
            url="/plus/dashboard?error=No+Real-Debrid+torrent+found+for+this+anime",
            status_code=302,
        )

    title = row["title"]
    rd_id = row["rd_torrent_id"]

    from omakase.plus.realdebrid import RealDebridClient

    rd_key = read_secret(db, user.id, "realdebrid_api_key")
    if not rd_key:
        return RedirectResponse(
            url="/plus/dashboard?error=Real-Debrid+API+key+not+configured",
            status_code=302,
        )

    client = RealDebridClient(rd_key)
    ok = await client.delete_torrent(rd_id)

    if ok:
        db.execute(
            "UPDATE anilist_plannings SET rd_torrent_id = '' WHERE user_id = ? AND anilist_id = ?",
            (user.id, anilist_id),
        )
        db.commit()
        msg = f'Removed "{title}" from Real-Debrid'
    else:
        msg = f'Failed+to+remove+"{title}"+from+Real-Debrid'

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
    from omakase.llm import list_backends

    stored = {}
    for key_name in _SECRET_KEYS:
        val = read_secret(db, user.id, key_name)
        stored[key_name] = "••••••••" if val else ""

    # Exclude the backend selector from the API key list — it's rendered separately
    api_keys = {k: v for k, v in _SECRET_KEYS.items() if k != "llm_backend"}
    api_stored = {k: v for k, v in stored.items() if k != "llm_backend"}

    stored_backend = read_secret(db, user.id, "llm_backend") or "openai"
    backends = sorted(list_backends())

    anilist_username = read_secret(db, user.id, "anilist_username") or ""

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "email": user.email,
            "keys": api_keys,
            "stored": api_stored,
            "saved": saved,
            "backends": backends,
            "stored_backend": stored_backend,
            "anilist_username": anilist_username,
        },
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

    # AniList username is a plain (non-masked) preference, not a secret key —
    # store the submitted value directly, or clear it when blanked.
    anilist_username = form.get("anilist_username", "").strip()
    if anilist_username:
        store_secret(db, user_id, "anilist_username", anilist_username)
        saved_keys.append("anilist_username")
    elif "anilist_username" in form:
        delete_secret(db, user_id, "anilist_username")

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


@router.post("/api/feedback")
async def feedback_api(
    request: Request,
    db=Depends(get_db),
    user=Depends(require_user),
):
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid JSON body"}
    if not isinstance(body, dict):
        return {"status": "error", "detail": "Invalid JSON body"}

    feedback_type = body.get("feedback_type", "")
    title = body.get("title", "")
    source = body.get("source")

    if not isinstance(feedback_type, str):
        return {"status": "error", "detail": "feedback_type must be a string"}
    if not isinstance(title, str):
        return {"status": "error", "detail": "title must be a string"}
    if source is None:
        source = "anilist"
    elif not isinstance(source, str):
        return {"status": "error", "detail": "source must be a string"}
    if not title:
        return {"status": "error", "detail": "title is required"}

    try:
        media_id = _coerce_optional_int(body.get("media_id"), "media_id")
        run_id = _coerce_optional_int(body.get("run_id"), "run_id")
    except ValueError as e:
        return {"status": "error", "detail": str(e)}

    if run_id is not None:
        run_row = db.execute(
            "SELECT id FROM run_history WHERE id = ? AND user_id = ?",
            (run_id, user.id),
        ).fetchone()
        if run_row is None:
            return {"status": "error", "detail": "run_id is invalid"}

    try:
        save_feedback(db, user.id, source, media_id, title, feedback_type, run_id)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok"}


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
