# Contributing to Omakase

Thanks for considering a contribution! Omakase is a small, hackable codebase — most additions land in well under 50 lines.

## The two extension points

Everything interesting in Omakase plugs into one of two registries:

### Adding an LLM backend

1. Create `src/omakase/llm/<your_backend>.py` subclassing `BaseLLM`.
2. Implement `generate(prompt, temperature, num_ctx, supports_json) -> str` — returning raw model output (ideally JSON).
3. Register it in `src/omakase/llm/__init__.py` with `register("<name>")(YourLLM)`.
4. Add a default URL to `DEFAULT_URLS` and fast/pro presets to `MODEL_PRESETS` in `src/omakase/types.py`.

If the backend exposes `/v1/chat/completions` in OpenAI's shape, just register `OpenAILLM` under a new name — that's how DeepSeek, Groq, Together, OpenRouter, and LM Studio work today.

### Adding a data source

1. Create `src/omakase/adapters/<your_source>.py` subclassing `SourceAdapter`.
2. Implement `fetch(username, pool_size=100, **kwargs) -> SourceData`.
3. Add `@register("<source-name>")` above the class.
4. Re-export from `src/omakase/adapters/__init__.py` so the decorator runs at import time.

## Dev setup

```bash
git clone https://github.com/HeyiTzSenpai/omakase
cd omakase
pip install -e ".[dev]"
pytest
ruff check .
```

## Submitting a change

- Open a PR against `main` with a focused commit.
- Add tests for new logic (`tests/test_*.py`) — the existing tests show the style (no network mocks needed for most things).
- Run `ruff check .` and `pytest` locally before pushing; CI runs both on Python 3.10–3.13.
- For a new backend, please include a row in the README's backend table and a registry test in `tests/test_llm_registry.py`.

## Scope

Omakase is anime-only by design. PRs that broaden the framing to "general media" will likely be declined — there are other tools for that. PRs that deepen anime-specific quality (better prompt engineering, smarter candidate selection, watchlist export, season-aware filtering) are very welcome.
