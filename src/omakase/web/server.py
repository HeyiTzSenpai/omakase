"""FastAPI web server for the Omakase setup UI.

Run with:  omakase web
Or:        python -m omakase web
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from omakase import __version__
from omakase.adapters.base import list_sources
from omakase.adapters.myanimelist import MALExportError
from omakase.engine import EmptyHistoryError
from omakase.engine import run as run_pipeline
from omakase.llm import list_backends
from omakase.types import DEFAULT_URLS, MODEL_PRESETS, OmakaseConfig, resolve_model_preset

# Hard cap on the uploaded MAL export. A 5000-entry list compresses to
# well under 1 MB; this is the "user uploaded the wrong file" guardrail.
_MAX_EXPORT_BYTES = 10 * 1024 * 1024

_HOSTED_PROVIDERS = {
    "openai": DEFAULT_URLS["openai"],
    "anthropic": DEFAULT_URLS["anthropic"],
    "gemini": DEFAULT_URLS["gemini"],
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


def _validate_hosted_provider(req: RecommendRequest) -> None:
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


@app.post("/api/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest):
    _validate_hosted_provider(req)
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

    cfg = OmakaseConfig(
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

    try:
        recs = run_pipeline(cfg)
    except MALExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except EmptyHistoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=(
                "Omakase could not reach the selected model provider. "
                "Check the provider status and try again."
            ),
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=(
                "The LLM took too long to respond. Try the Fast mode, a smaller model, "
                "or a different backend."
            ),
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=_friendly_llm_error(e, req.llm_type, req.model))
    except Exception as e:
        logger.error("Recommendation request failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=500,
            detail="Omakase could not finish this menu. Try again shortly.",
        )

    return RecommendResponse(
        source=req.source,
        username=req.username,
        recommendations=[
            RecommendationOut(
                title=r.title,
                predicted_score=r.predicted_score,
                reasoning=r.reasoning,
                best_match_from_history=r.best_match_from_history,
                url=r.url,
                source=r.source,
            )
            for r in recs
        ],
    )


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
    if code in (401, 403):
        return (
            f"{llm_type.title()} rejected your API key. "
            "Double-check the key you pasted, or that it has access to this model."
        )
    if code == 404:
        return (
            f"{llm_type.title()} doesn't recognize the model '{model}'. "
            "Try a different model or switch the Fast/Pro toggle."
        )
    if code == 429:
        return (
            f"{llm_type.title()} rate-limited your key. Wait a minute, "
            "or try a different backend / your own paid key."
        )
    if code >= 500:
        return (
            f"{llm_type.title()} is having issues right now (HTTP {code}). Try again in a moment."
        )
    return f"{llm_type.title()} could not complete the request (HTTP {code})."


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """Launch the Omakase web UI server."""
    print(f"  Omakase setup UI -> http://{host}:{port}")
    print("  Press Ctrl+C to stop")
    uvicorn.run(app, host=host, port=port, log_level="info")
