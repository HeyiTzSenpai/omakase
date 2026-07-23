# Omakase

> お任せ means "I'll leave it to the chef."

Omakase is an anime sommelier. It pairs your scored watch history with a short description of your taste, then asks a model you choose to prepare a small recommendation menu with a reason for every pick.

[Try the public counter](https://omakase.jhinx.dev) · [Read the case study](https://jhinx.dev/projects/omakase) · [Visit jhinx.dev](https://jhinx.dev)

[![CI](https://github.com/HeyiTzSenpai/omakase/actions/workflows/ci.yml/badge.svg)](https://github.com/HeyiTzSenpai/omakase/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

![A mystical midnight tasting counter with five dishes connected by constellation light](src/omakase/web/static/generated/omakase-counter-v2.png)

## What makes it different

Most recommendation systems know what is popular. Omakase tries to understand why a story worked for you.

It combines:

- your AniList history or MyAnimeList export;
- the scores and statuses already in that history;
- optional notes about themes, moods, characters, and patterns you value;
- an unwatched candidate pool;
- a model selected and paid for by you.

The result is a short tasting menu instead of an endless popularity feed. Each recommendation includes a predicted fit, an explanation, and a title from your history that helped shape the match.

## Public counter

The hosted site supports request-local keys for OpenAI, Anthropic, Gemini, and OpenRouter. There is no Omakase account.

Your provider key, history, and taste notes are used only for the current request by Omakase. They are not written to disk, logs, cookies, or a database. Your history and notes are sent to the provider you select so it can generate the menu. That provider's own data policy still applies.

The hosted counter accepts a public AniList username or a MyAnimeList XML export. Local model addresses are intentionally unavailable there because a public server cannot safely or honestly connect to a model running on your computer.

## Run it yourself

Self-hosting unlocks local Ollama and LM Studio models as well as the supported cloud providers.

```bash
pip install omakase

# Create a starter taste profile
omakase init

# Use local Ollama with your AniList history
omakase recommend -u your-anilist-handle

# Or open the browser interface
omakase web
```

Install from source when developing:

```bash
git clone https://github.com/HeyiTzSenpai/omakase
cd omakase
pip install -e .
```

## Choose a model

The command line supports local and cloud backends. Cloud credentials can use `OMAKASE_API_KEY` or the provider-specific environment variable.

```bash
# OpenAI
export OMAKASE_API_KEY=your-key
omakase recommend -u your-handle --llm-type openai --mode pro

# Anthropic
export OMAKASE_API_KEY=your-key
omakase recommend -u your-handle --llm-type anthropic --mode pro

# Gemini
export OMAKASE_API_KEY=your-key
omakase recommend -u your-handle --llm-type gemini
```

Supported backends include Ollama, LM Studio, OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, and Together. Use `--model` to override a preset.

## The pipeline

```text
taste notes + scored history + unwatched candidates
                         |
                         v
                  selected model
                         |
                         v
       ranked picks + reasoning + history pairing
```

Omakase keeps its extension points small. Source adapters normalize watch history and candidates. Model adapters handle provider protocols. The recommendation engine builds one prompt and returns the same structured result to the terminal, JSON output, or browser interface.

## Commands

| Command | Purpose |
|---|---|
| `omakase recommend -u <user>` | Prepare a recommendation menu |
| `omakase web` | Start the browser interface |
| `omakase init` | Create a starter taste profile |
| `omakase sources` | List available history sources |
| `omakase backends` | List available model backends |

Run `omakase recommend --help` for all options. See [CONTRIBUTING.md](CONTRIBUTING.md) for development and extension guidance.

## Scope

This public repository is the bring-your-own-key recommendation tool. Omakase Plus is a separate private experiment and is not connected to the hosted demo.

## License

MIT. See [LICENSE](LICENSE).
