"""FastAPI web server for the Omakase setup UI.

Run with:  omakase web
Or:        python -m omakase web
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from omakase import __version__
from omakase.adapters.base import list_sources
from omakase.engine import EmptyHistoryError
from omakase.engine import run as run_pipeline
from omakase.llm import list_backends
from omakase.types import DEFAULT_URLS, MODEL_PRESETS, OmakaseConfig, resolve_model_preset

_HERE = Path(__file__).resolve().parent

app = FastAPI(title="Omakase", version=__version__)

app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


# ── Request / response models ─────────────────────────────


class RecommendRequest(BaseModel):
    llm_type: str = "ollama"
    llm_url: str = "http://localhost:11434"
    api_key: str | None = ""  # LLM API key
    mal_client_id: str | None = ""  # MAL Client ID (source-specific)
    model: str = "qwen2.5:7b"
    source: str = "anilist"
    username: str = ""
    profile: str = ""
    pool_size: int = 100
    temperature: float = 0.4
    mode: str = "fast"
    use_planning: bool = False


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
    return html


@app.post("/api/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest):
    if not req.username.strip():
        raise HTTPException(status_code=400, detail="Username is required")
    if not req.profile.strip() and not req.use_planning:
        raise HTTPException(
            status_code=400,
            detail="Taste profile is required (or enable Plan to Watch)",
        )

    # Persist the inline profile to a file so the engine can re-read it.
    profile_path = Path.home() / ".omakase" / "profile.md"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(req.profile, encoding="utf-8")

    # Stage credentials in env for downstream clients to pick up.
    if req.api_key:
        os.environ["OMAKASE_API_KEY"] = req.api_key
    if req.mal_client_id:
        os.environ["MAL_CLIENT_ID"] = req.mal_client_id

    llm_url, llm_type, model, supports_json = resolve_model_preset(
        req.llm_url,
        req.llm_type,
        req.model,
        req.mode,
    )

    cfg = OmakaseConfig(
        source=req.source,
        username=req.username.strip(),
        llm_url=llm_url,
        model=model,
        profile_path=str(profile_path),
        candidate_pool_size=req.pool_size,
        temperature=req.temperature,
        llm_type=llm_type,
        mode=req.mode,
        supports_json_mode=supports_json,
        use_planning=req.use_planning,
    )

    try:
        recs = run_pipeline(cfg)
    except EmptyHistoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Couldn't reach the LLM at {req.llm_url}. "
                "If this is a local backend (Ollama / LM Studio), is it running? "
                "Otherwise check the base URL."
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
        raise HTTPException(status_code=500, detail=str(e))

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
- Stories that earn their ending — strong third act

## Things I bounce off
- [What do you dislike?]

## Characters that resonate
- [Character] — [why they resonate]

## Recent loves (don't recommend these — just calibration)
- [Title you scored highly]"""


def _get_default_profile() -> str:
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
    body = e.response.text[:300]
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
    return f"{llm_type.title()} returned HTTP {code}: {body}"


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """Launch the Omakase web UI server."""
    print(f"  Omakase setup UI -> http://{host}:{port}")
    print("  Press Ctrl+C to stop")
    uvicorn.run(app, host=host, port=port, log_level="info")
