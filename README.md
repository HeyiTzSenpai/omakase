# Omakase 🍣

> **お任せ** — *"I'll leave it to the chef."*
> An LLM-powered sommelier for anime. Bring your own list, bring your own model, get a tasting menu.

### **[Try the live demo →](https://omakase.jhinx.dev)**  ·  [jhinx.dev](https://jhinx.dev)

[![CI](https://github.com/HeyiTzSenpai/omakase/actions/workflows/ci.yml/badge.svg)](https://github.com/HeyiTzSenpai/omakase/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

```bash
# Local Ollama, AniList list, recommendations in your terminal
omakase recommend -u your-anilist-handle

# Or fire up the web UI
omakase web
```

---

## Why?

Algorithmic anime recs (AniList, MAL, streaming services) optimize for engagement and popularity — not your actual taste. You've scored 200 anime, given a dozen of them a 9 or 10, and yet you keep getting served the same trending shows everyone is already watching.

Omakase takes a different approach: **you write a short markdown profile** describing what you love and what you bounce off, **Omakase pairs it with your scored history**, and an LLM of *your choice* reasons over the combination to pick 5–10 anime from a candidate pool. You get a "why" for each pick — tied to specific shows you've already rated.

Think of it as the difference between a vending machine and a chef who's watched you eat for a while.

## How it works

```
Your taste profile (markdown) ─┐
Your scored anime history ─────┤──→ prompt ──→ any LLM ──→ tasting menu
Candidate pool (unwatched) ────┘
```

1. **Pick a source** — AniList or MyAnimeList
2. **Fetch your history** — scored entries, genres, tags, studios, dropped/paused
3. **Fetch candidates** — popular titles you haven't seen, or your own Plan to Watch
4. **Build a prompt** — your written profile + scored history + candidate pool
5. **Ask any LLM** — local (Ollama, LM Studio) or cloud (Anthropic, OpenAI, Gemini, DeepSeek, OpenRouter, Groq, Together)
6. **Get your tasting menu** — terminal table, JSON, or browser UI

## Install

```bash
pip install omakase
```

Or from source:

```bash
git clone https://github.com/HeyiTzSenpai/omakase
cd omakase
pip install -e .
```

## Quick start

### 1. Create a taste profile

```bash
omakase init
```

This drops a starter `taste-profile.md` in the current directory. Edit it — be specific. Mention shows, studios, character archetypes, and genres. The more honest and concrete it is, the better the picks.

### 2. Get recommendations

```bash
# Local — Ollama (free, private, runs on your machine)
omakase recommend -u your-anilist-handle

# OpenAI
export OMAKASE_API_KEY=sk-...
omakase recommend -u your-handle --llm-type openai --mode pro

# Anthropic Claude
export OMAKASE_API_KEY=sk-ant-...
omakase recommend -u your-handle --llm-type anthropic --mode pro

# Google Gemini
export OMAKASE_API_KEY=...
omakase recommend -u your-handle --llm-type gemini

# Or web UI — set everything in your browser
omakase web
```

### 3. Iterate

If a recommendation misses badly, add a line to your taste profile explaining why. Over 4–6 weeks the picks sharpen noticeably.

## Commands

| Command | Description |
|---|---|
| `omakase recommend -u <user>` | Run the recommendation pipeline |
| `omakase web` | Launch the browser-based setup UI |
| `omakase init` | Create a starter taste profile |
| `omakase sources` | List available data sources |
| `omakase backends` | List available LLM backends |

Run `omakase recommend --help` for the full flag set.

## Supported LLM backends

| Backend | Local / Cloud | API key env var | Default URL |
|---|---|---|---|
| **Ollama** | Local | — | `http://localhost:11434` |
| **LM Studio** | Local | — | `http://localhost:1234` |
| **OpenAI** | Cloud | `OMAKASE_API_KEY` or `OPENAI_API_KEY` | `https://api.openai.com` |
| **Anthropic** | Cloud | `OMAKASE_API_KEY` or `ANTHROPIC_API_KEY` | `https://api.anthropic.com` |
| **Gemini** | Cloud | `OMAKASE_API_KEY` or `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com` |
| **DeepSeek** | Cloud | `OMAKASE_API_KEY` or `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| **OpenRouter** | Cloud (aggregator) | `OMAKASE_API_KEY` or `OPENROUTER_API_KEY` | `https://openrouter.ai/api` |
| **Groq** | Cloud (fast inference) | `OMAKASE_API_KEY` or `GROQ_API_KEY` | `https://api.groq.com/openai` |
| **Together** | Cloud (aggregator) | `OMAKASE_API_KEY` or `TOGETHER_API_KEY` | `https://api.together.xyz` |

Each backend has a `--mode fast` and `--mode pro` preset (cheap+quick vs. better-reasoning). Override the model directly with `--model <name>` if you want something specific.

## Configuration via env vars

| Variable | Default | Description |
|---|---|---|
| `OMAKASE_API_KEY` | — | Catch-all LLM API key (checked first for every cloud backend) |
| `OMAKASE_LLM_URL` | `http://localhost:11434` | Default LLM API base URL |
| `OMAKASE_MODEL` | `qwen2.5:7b` | Default model name |
| `OMAKASE_PORT` | `8765` | Web UI port |
| `MAL_CLIENT_ID` | — | Required when using the MyAnimeList source |

Backend-specific env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `TOGETHER_API_KEY`) are also respected as fallbacks.

## Extending

See [CONTRIBUTING.md](CONTRIBUTING.md) for the two extension points (LLM backends + source adapters). The architecture is small on purpose — adding a new backend is usually ~40 lines.

## License

MIT. See [LICENSE](LICENSE).
<!-- CodeRabbit trigger smoke: 2026-05-26. Close this PR unmerged after review proof. -->
