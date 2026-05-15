"""Core data types and model presets for Omakase."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MediaItem:
    """A single anime — either from the user's history or a candidate."""

    id: int
    title_romaji: str
    title_english: str | None = None
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    score: float | None = None  # user's score (history only)
    status: str | None = None  # CURRENT, COMPLETED, DROPPED, etc. (history only)
    format: str | None = None  # TV, Movie, OVA, etc.
    episodes: int | None = None
    studio: str | None = None
    description: str | None = None  # only for candidates
    mean_score: float | None = None  # global average score


@dataclass
class Recommendation:
    """A single recommendation from the LLM."""

    title: str
    predicted_score: float
    reasoning: str
    best_match_from_history: str
    url: str | None = None
    source: str | None = None


@dataclass
class SourceData:
    """Normalized data from any source adapter."""

    username: str
    history: list[MediaItem]
    candidates: list[MediaItem]
    source_name: str  # e.g. "anilist", "myanimelist"


@dataclass
class OmakaseConfig:
    """Runtime configuration for a recommendation run."""

    source: str
    username: str
    llm_url: str
    model: str
    profile_path: str
    candidate_pool_size: int = 100
    temperature: float = 0.4
    num_ctx: int = 16384
    llm_type: str = "ollama"
    output_format: str = "terminal"  # terminal, json
    mode: str = "fast"  # fast or pro — controls model preset
    supports_json_mode: bool = True  # some models don't support response_format
    use_planning: bool = False  # use user's Planning list as candidates


# Default API base URLs for each backend.
DEFAULT_URLS: dict[str, str] = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://localhost:1234",
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api",
    "groq": "https://api.groq.com/openai",
    "together": "https://api.together.xyz",
}


# Model presets for fast/pro modes. supports_json controls whether we ask the
# backend for guaranteed JSON output (response_format). Some backends/models
# (e.g. DeepSeek reasoner, Anthropic, certain aggregator routes) need looser
# parsing instead.
MODEL_PRESETS: dict[str, dict[str, str]] = {
    # Local
    "ollama-fast": {"model": "qwen2.5:7b", "supports_json": "true"},
    "ollama-pro": {"model": "qwen2.5:14b", "supports_json": "true"},
    "lmstudio-fast": {"model": "qwen2.5-7b-instruct", "supports_json": "true"},
    "lmstudio-pro": {"model": "qwen2.5-14b-instruct", "supports_json": "true"},
    # OpenAI
    "openai-fast": {"model": "gpt-4o-mini", "supports_json": "true"},
    "openai-pro": {"model": "gpt-4o", "supports_json": "true"},
    # Anthropic
    "anthropic-fast": {"model": "claude-haiku-4-5", "supports_json": "false"},
    "anthropic-pro": {"model": "claude-sonnet-4-6", "supports_json": "false"},
    # Google
    "gemini-fast": {"model": "gemini-2.5-flash", "supports_json": "true"},
    "gemini-pro": {"model": "gemini-2.5-pro", "supports_json": "true"},
    # DeepSeek
    "deepseek-fast": {"model": "deepseek-chat", "supports_json": "false"},
    "deepseek-pro": {"model": "deepseek-reasoner", "supports_json": "false"},
    # Aggregators (OpenAI-compatible)
    "openrouter-fast": {"model": "openai/gpt-4o-mini", "supports_json": "true"},
    "openrouter-pro": {"model": "anthropic/claude-sonnet-4.6", "supports_json": "false"},
    "groq-fast": {"model": "llama-3.3-70b-versatile", "supports_json": "true"},
    "groq-pro": {"model": "deepseek-r1-distill-llama-70b", "supports_json": "false"},
    "together-fast": {"model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "supports_json": "true"},
    "together-pro": {"model": "deepseek-ai/DeepSeek-R1", "supports_json": "false"},
}

# Models the CLI considers "default" — if the user passes one of these we treat
# it as 'no model specified' and substitute the preset's model.
DEFAULT_MODEL_SENTINELS = {"qwen2.5:7b", "qwen2.5:14b"}


def resolve_model_preset(
    url: str,
    llm_type: str,
    model: str,
    mode: str,
) -> tuple[str, str, str, bool]:
    """Resolve a (url, llm_type, model, supports_json) tuple from the chosen preset.

    - If `url` is the Ollama default, substitute the canonical URL for the backend.
    - If `model` is a default sentinel, substitute the preset's model.
    - `supports_json` always comes from the preset when one exists.
    """
    if url == "http://localhost:11434" and llm_type in DEFAULT_URLS:
        url = DEFAULT_URLS[llm_type]

    key = f"{llm_type}-{mode}"
    preset = MODEL_PRESETS.get(key)
    if preset is None:
        return url, llm_type, model, True

    resolved_model = preset["model"] if model in DEFAULT_MODEL_SENTINELS else model
    return url, llm_type, resolved_model, preset["supports_json"] == "true"
