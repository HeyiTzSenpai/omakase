"""FastAPI web server for the Omakase setup UI.

Run with:  omakase web
Or:        python -m omakase web
"""

from __future__ import annotations

import base64
import concurrent.futures
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.error import HTTPError

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from omakase import __version__
from omakase.adapters.base import list_sources
from omakase.adapters.myanimelist import CandidateSourceError, MALExportError
from omakase.engine import EmptyHistoryError, RecommendationOutputError
from omakase.engine import run as run_pipeline
from omakase.lite import db as lite_db
from omakase.lite import routes as account_routes
from omakase.lite.routes import api_router as account_api_router
from omakase.lite.routes import page_router as account_page_router
from omakase.llm import list_backends
from omakase.types import DEFAULT_URLS, MODEL_PRESETS, OmakaseConfig, resolve_model_preset

# Hard cap on the uploaded MAL export. A 5000-entry list compresses to
# well under 1 MB; this is the "user uploaded the wrong file" guardrail.
_MAX_EXPORT_BYTES = 10 * 1024 * 1024
_JOB_TTL_SECONDS = 60 * 60
_MAX_ACTIVE_JOBS = 3

_HOSTED_PROVIDERS = {
    "openai": DEFAULT_URLS["openai"],
    "anthropic": DEFAULT_URLS["anthropic"],
    "gemini": DEFAULT_URLS["gemini"],
    "deepseek": DEFAULT_URLS["deepseek"],
    "openrouter": DEFAULT_URLS["openrouter"],
}

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent


def _asset_version() -> str:
    """Cache-bust query string for the public interface assets.

    Built once at import time from the stylesheet's mtime — changes
    every time someone edits style.css and rebuilds the container,
    which is exactly when we need users to skip their browser cache.
    Falls back to the package version if stat() fails (e.g. inside an
    odd packaging scenario).
    """
    assets = (
        _HERE / "static" / "style.css",
        _HERE / "static" / "app.js",
        _HERE / "static" / "generated" / "omakase-counter-v2.png",
    )
    try:
        return str(max(int(asset.stat().st_mtime) for asset in assets))
    except OSError:
        return __version__


_ASSET_VERSION = _asset_version()

app = FastAPI(title="Omakase", version=__version__)

app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
app.include_router(account_page_router)
app.include_router(account_api_router)

_job_lock = threading.Lock()
_recommendation_jobs: dict[str, dict] = {}
_job_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_MAX_ACTIVE_JOBS,
    thread_name_prefix="omakase-recommend",
)


@app.middleware("http")
async def browser_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "font-src https://fonts.gstatic.com; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com"
    )
    if request.url.path.startswith(("/account", "/api/")):
        response.headers["Cache-Control"] = "no-store"
    if _is_hosted_public():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Request / response models ─────────────────────────────


class RecommendRequest(BaseModel):
    llm_type: str = "ollama"
    llm_url: str = "http://localhost:11434"
    api_key: str | None = ""  # LLM API key
    mal_client_id: str | None = ""  # MAL Client ID (source-specific)
    mal_export_b64: str | None = ""  # base64-encoded MAL XML export (alternative to Client ID)
    model: str = "qwen2.5:7b"
    source: str = "anilist"
    username: str = ""
    profile: str = ""
    pool_size: int = 100
    temperature: float = 0.4
    mode: str = "fast"
    use_planning: bool = False
    skip_profile: bool = False  # broader recs inferred from scoring history alone


class RecommendationOut(BaseModel):
    title: str
    predicted_score: float
    reasoning: str
    best_match_from_history: str
    url: str | None = None
    source: str | None = None


class RecommendResponse(BaseModel):
    source: str
    username: str
    recommendations: list[RecommendationOut]


# ── Routes ────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    path = _HERE / "templates" / "index.html"
    default_profile = _get_default_profile()
    html = path.read_text(encoding="utf-8")
    html = html.replace(
        "{% if default_profile %}{{ default_profile }}{% endif %}",
        _escape_html(default_profile),
    )
    # Cache-bust the CSS bundle on every deploy so users who had the
    # old template HTML never end up with the old stylesheet — the
    # layout of `.rec-card` switched from <div> to <a>, and stale CSS
    # rendered the new anchor cards as inline links with browser
    # default underlines. Version is derived from style.css mtime at
    # import time (see `_asset_version`).
    html = html.replace("{{ asset_version }}", _ASSET_VERSION)
    return html


@app.get("/favicon.ico")
async def favicon_ico():
    return FileResponse(_HERE / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "service": "omakase-public",
        "version": __version__,
        "sourceCommit": os.environ.get("OMAKASE_SOURCE_COMMIT", "development"),
    }


def _is_hosted_public() -> bool:
    return os.environ.get("OMAKASE_PUBLIC_HOSTED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _validate_hosted_provider(
    req: RecommendRequest,
    *,
    allow_deepseek_pro: bool = False,
) -> None:
    if not _is_hosted_public():
        return
    expected = _HOSTED_PROVIDERS.get(req.llm_type)
    if expected is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "The hosted demo supports OpenAI, Anthropic, Gemini, and OpenRouter. "
                "Run Omakase on your own machine to use a local model."
            ),
        )
    if req.llm_url.rstrip("/") != expected.rstrip("/"):
        raise HTTPException(
            status_code=400,
            detail="The hosted demo can contact only the official provider endpoint.",
        )
    if not (req.api_key or "").strip():
        raise HTTPException(status_code=400, detail="Paste your provider key to continue.")
    if req.llm_type == "deepseek":
        allowed = {("fast", "deepseek-v4-flash")}
        if allow_deepseek_pro:
            allowed.add(("pro", "deepseek-v4-pro"))
        if (req.mode, req.model) not in allowed:
            detail = (
                "Choose the standard DeepSeek Quick or Deep model."
                if allow_deepseek_pro
                else "DeepSeek Deep runs through the background recommendation endpoint."
            )
            raise HTTPException(status_code=400, detail=detail)


def _prepare_config(
    req: RecommendRequest,
    *,
    allow_deepseek_pro: bool = False,
) -> OmakaseConfig:
    _validate_hosted_provider(req, allow_deepseek_pro=allow_deepseek_pro)
    export_data: bytes | None = None
    if req.mal_export_b64:
        try:
            export_data = base64.b64decode(req.mal_export_b64, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="Uploaded export couldn't be decoded. Try re-uploading the file.",
            )
        if len(export_data) > _MAX_EXPORT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Export file is too large ({len(export_data) // 1024} KB). "
                    f"Max is {_MAX_EXPORT_BYTES // (1024 * 1024)} MB. "
                    "are you sure you uploaded the right file?"
                ),
            )
        if req.source != "myanimelist":
            raise HTTPException(
                status_code=400,
                detail="Export upload is currently MAL-only. Switch the Source to MyAnimeList.",
            )

    if not export_data and not req.username.strip():
        raise HTTPException(status_code=400, detail="Username is required")
    if _is_hosted_public() and req.source == "myanimelist" and not export_data:
        raise HTTPException(
            status_code=400,
            detail="The hosted demo uses a MyAnimeList export so your list can stay private.",
        )
    if not req.profile.strip() and not req.use_planning and not req.skip_profile:
        raise HTTPException(
            status_code=400,
            detail=(
                "Taste profile is required. Enable Plan to Watch, or check "
                "'Skip profile' for broader recs from your scores alone."
            ),
        )

    llm_url, llm_type, model, supports_json = resolve_model_preset(
        req.llm_url,
        req.llm_type,
        req.model,
        req.mode,
    )

    return OmakaseConfig(
        source=req.source,
        username=req.username.strip() or "uploaded-list",
        llm_url=llm_url,
        model=model,
        profile_path="",
        candidate_pool_size=req.pool_size,
        temperature=req.temperature,
        llm_type=llm_type,
        mode=req.mode,
        supports_json_mode=supports_json,
        use_planning=req.use_planning,
        export_data=export_data,
        taste_profile="" if req.skip_profile else req.profile,
        api_key=req.api_key,
        mal_client_id=req.mal_client_id if not export_data else None,
    )


def _run_config(req: RecommendRequest, cfg: OmakaseConfig):
    try:
        return run_pipeline(cfg)
    except MALExportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except EmptyHistoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CandidateSourceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.ConnectError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                "Omakase could not reach the selected model provider. "
                "Check the provider status and try again."
            ),
        ) from e
    except httpx.TimeoutException as e:
        raise HTTPException(
            status_code=504,
            detail=(
                "The LLM took too long to respond. Try the Fast mode, a smaller model, "
                "or a different backend."
            ),
        ) from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=_friendly_llm_error(e, req.llm_type, req.model),
        ) from e
    except RecommendationOutputError as e:
        raise HTTPException(
            status_code=502,
            detail=("The selected model returned an incomplete menu. Try again, or use Fast mode."),
        ) from e
    except Exception as e:
        if isinstance(e, HTTPError) and e.code == 404 and req.source == "anilist":
            raise HTTPException(
                status_code=400,
                detail=(
                    "AniList could not find that user. Check the username "
                    "and make sure the anime list is public."
                ),
            ) from e
        logger.error("Recommendation request failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="Omakase could not finish this menu. Try again shortly.",
        ) from e


def _recommendations_out(recs) -> list[RecommendationOut]:
    return [
        RecommendationOut(
            title=rec.title,
            predicted_score=rec.predicted_score,
            reasoning=rec.reasoning,
            best_match_from_history=rec.best_match_from_history,
            url=rec.url,
            source=rec.source,
        )
        for rec in recs
    ]


@app.post("/api/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest):
    cfg = _prepare_config(req)
    recs = _run_config(req, cfg)

    return RecommendResponse(
        source=req.source,
        username=req.username,
        recommendations=_recommendations_out(recs),
    )


def _prune_jobs_locked(now: float) -> None:
    expired = [
        job_id
        for job_id, job in _recommendation_jobs.items()
        if job["status"] in {"done", "error", "cancelled"}
        and now - job["updated_at"] > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        _recommendation_jobs.pop(job_id, None)


def reset_recommendation_jobs() -> None:
    """Forget job receipts and ask in-flight test jobs not to persist results."""
    with _job_lock:
        for job in _recommendation_jobs.values():
            job["cancel_requested"] = True
        _recommendation_jobs.clear()


def recommendation_jobs_debug_snapshot() -> str:
    """Return receipt-only job state for privacy regression tests."""
    with _job_lock:
        return repr(_recommendation_jobs)


def _finish_job(
    job_id: str,
    *,
    status: str,
    recommendations: list[dict] | None = None,
    account_saved: bool = False,
    detail: str | None = None,
    status_code: int | None = None,
) -> None:
    with _job_lock:
        job = _recommendation_jobs.get(job_id)
        if job is None:
            return
        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            job["updated_at"] = time.monotonic()
            return
        job["status"] = status
        job["updated_at"] = time.monotonic()
        job["recommendations"] = recommendations or []
        job["account_saved"] = account_saved
        job["detail"] = detail
        job["status_code"] = status_code


def _run_recommendation_job(
    job_id: str,
    *,
    req: RecommendRequest,
    cfg: OmakaseConfig,
    user_id: int | None,
) -> None:
    try:
        with _job_lock:
            job = _recommendation_jobs.get(job_id)
            if job is None or job.get("cancel_requested"):
                return
            job["status"] = "running"
            job["updated_at"] = time.monotonic()
        recs = _run_config(req, cfg)
        with _job_lock:
            job = _recommendation_jobs.get(job_id)
            if job is None or job.get("cancel_requested"):
                return

        account_saved = False
        if user_id is not None:
            conn = lite_db.connect()
            try:
                _, serialized = lite_db.save_recommendation_run(
                    conn,
                    user_id=user_id,
                    source=cfg.source,
                    source_username=cfg.username,
                    provider=cfg.llm_type,
                    model=cfg.model,
                    mode=cfg.mode,
                    recommendations=recs,
                )
                account_saved = True
            finally:
                conn.close()
        else:
            serialized = [item.model_dump() for item in _recommendations_out(recs)]
        _finish_job(
            job_id,
            status="done",
            recommendations=serialized,
            account_saved=account_saved,
        )
    except HTTPException as exc:
        _finish_job(
            job_id,
            status="error",
            detail=str(exc.detail),
            status_code=exc.status_code,
        )
    finally:
        cfg.api_key = None
        cfg.export_data = None
        req.api_key = None
        req.mal_export_b64 = None


@app.post("/api/recommend/jobs", status_code=202)
def start_recommendation_job(req: RecommendRequest, request: Request):
    account_routes.enforce_rate_limit(
        request,
        action="recommend-job",
        limit=10,
        window_seconds=60 * 60,
    )
    user = account_routes.request_user(request, require_csrf=True)
    effective_req = req
    if user is not None:
        conn = lite_db.connect()
        try:
            stored_profile = lite_db.get_profile(conn, user.id)
            if not req.profile.strip() and not req.skip_profile and stored_profile:
                effective_req = req.model_copy(update={"profile": stored_profile})
        finally:
            conn.close()

    cfg = _prepare_config(effective_req, allow_deepseek_pro=True)
    if user is not None:
        conn = lite_db.connect()
        try:
            if req.profile.strip() and not req.skip_profile:
                lite_db.update_profile(conn, user.id, req.profile)
            feedback = lite_db.feedback_context(conn, user.id)
            if feedback:
                cfg.taste_profile = "\n\n".join(
                    part for part in (cfg.taste_profile or "", feedback) if part
                )
        finally:
            conn.close()

    now = time.monotonic()
    with _job_lock:
        _prune_jobs_locked(now)
        active = sum(
            job["status"] in {"queued", "running"} for job in _recommendation_jobs.values()
        )
        if active >= _MAX_ACTIVE_JOBS:
            raise HTTPException(
                status_code=429,
                detail="The counter is full. Wait for another menu to finish and try again.",
            )
        job_id = secrets.token_urlsafe(24)
        _recommendation_jobs[job_id] = {
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "source": req.source,
            "username": req.username,
            "recommendations": [],
            "account_saved": False,
            "detail": None,
            "status_code": None,
            "cancel_requested": False,
        }
    _job_executor.submit(
        _run_recommendation_job,
        job_id,
        req=effective_req,
        cfg=cfg,
        user_id=user.id if user else None,
    )
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/recommend/jobs/{job_id}")
def recommendation_job(job_id: str):
    with _job_lock:
        _prune_jobs_locked(time.monotonic())
        job = _recommendation_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Recommendation job not found.")
        return {
            "status": job["status"],
            "source": job["source"],
            "username": job["username"],
            "recommendations": job["recommendations"],
            "account_saved": job["account_saved"],
            "detail": job["detail"],
            "status_code": job["status_code"],
        }


@app.delete("/api/recommend/jobs/{job_id}")
def cancel_recommendation_job(job_id: str):
    with _job_lock:
        job = _recommendation_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Recommendation job not found.")
        if job["status"] in {"queued", "running"}:
            job["cancel_requested"] = True
            job["status"] = "cancelled"
            job["updated_at"] = time.monotonic()
    return {"status": job["status"]}


@app.get("/api/profile")
async def get_profile():
    """Load taste profile from the default locations."""
    if _is_hosted_public():
        raise HTTPException(status_code=404, detail="Not found")
    candidates = [
        Path.cwd() / "taste-profile.md",
        Path.home() / ".omakase" / "profile.md",
    ]
    for p in candidates:
        if p.exists():
            return {"profile": p.read_text(encoding="utf-8")}
    raise HTTPException(status_code=404, detail="No taste profile found")


@app.get("/api/sources")
async def get_sources():
    return {"sources": list_sources()}


@app.get("/api/backends")
async def get_backends():
    """Return registered LLM backends with default URLs + presets."""
    presets: dict[str, dict[str, str]] = {}
    for k, v in MODEL_PRESETS.items():
        presets[k] = {"model": v["model"], "supports_json": v["supports_json"]}
    return {
        "backends": list_backends(),
        "default_urls": DEFAULT_URLS,
        "presets": presets,
    }


@app.get("/api/models")
async def discover_models(url: str = "http://localhost:1234"):
    """Discover models from any OpenAI-compatible /v1/models endpoint."""
    if _is_hosted_public():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url.rstrip('/')}/v1/models")
            resp.raise_for_status()
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            return {"models": sorted(models)}
    except Exception:
        return {"models": []}


# ── Helpers ───────────────────────────────────────────────

_DEFAULT_PROFILE_FALLBACK = """## Things I love
- Morally complex protagonists, not pure-hearted heroes
- Dense world-building over slice-of-life
- Stories that earn their ending, especially a strong third act

## Things I bounce off
- [What do you dislike?]

## Characters that resonate
- [Character]: [why they resonate]

## Recent loves (do not recommend these, they are calibration)
- [Title you scored highly]"""

_PUBLIC_PROFILE_STARTER = """## What usually works for me
- Thoughtful science fiction and fantasy
- Character growth that takes its time

## What I usually avoid
- Stories that rely on shock without earning it

## A few favorites
- Add two or three titles and what stayed with you"""


def _get_default_profile() -> str:
    if _is_hosted_public():
        # The public demo must never render a profile that happens to exist on
        # the host machine or inside a reused deployment directory.
        return _PUBLIC_PROFILE_STARTER
    candidates = [
        Path.cwd() / "taste-profile.md",
        Path.home() / ".omakase" / "profile.md",
        _HERE.parent.parent.parent / "examples" / "taste-profile.md",
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return _DEFAULT_PROFILE_FALLBACK


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _friendly_llm_error(e: httpx.HTTPStatusError, llm_type: str, model: str) -> str:
    """Translate a raw LLM HTTP error into a message the visitor can act on."""
    code = e.response.status_code
    provider = "DeepSeek" if llm_type == "deepseek" else llm_type.title()
    if code in (401, 403):
        return (
            f"{provider} rejected your API key. "
            "Double-check the key you pasted, or that it has access to this model."
        )
    if code == 404:
        return (
            f"{provider} doesn't recognize the model '{model}'. "
            "Try a different model or switch the Fast/Pro toggle."
        )
    if code == 429:
        return (
            f"{provider} rate-limited your key. Wait a minute, "
            "or try a different backend / your own paid key."
        )
    if code >= 500:
        return f"{provider} is having issues right now (HTTP {code}). Try again in a moment."
    return f"{provider} could not complete the request (HTTP {code})."


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """Launch the Omakase web UI server."""
    print(f"  Omakase setup UI -> http://{host}:{port}")
    print("  Press Ctrl+C to stop")
    uvicorn.run(app, host=host, port=port, log_level="info")
