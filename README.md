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

The hosted site supports request-local keys for OpenAI, Anthropic, Gemini, DeepSeek, and OpenRouter. Both Quick and Deep model presets run as background jobs, so a slower reasoning model can finish without holding one proxy request open. Choose the provider explicitly: DeepSeek and OpenAI keys can share the same `sk-` shape, so key text alone is not a safe provider signal.

Guest menus remain request-local. The provider key, history, and taste notes are not written to disk, logs, cookies, or a database. Your history and notes are sent to the provider you select so it can generate the menu. That provider's own data policy still applies.

The hosted counter accepts a public AniList username or a MyAnimeList XML export. Local model addresses are intentionally unavailable there because a public server cannot safely or honestly connect to a model running on your computer.

### Omakase Lite accounts

People can request recommendation-only access from the public counter. The owner reviews requests in a private inbox and shares a one-time, seven-day invitation. A Lite account adds:

- saved recommendation history and a local My List;
- Not Interested, Add to My List, and Already Watched feedback;
- a saved taste note and feedback context for future menus;
- no Plex, download, acquisition, or private Plus access.

Lite saves completed recommendations, the account profile, and feedback in its own SQLite database. Provider keys and uploaded MAL files remain in memory only and are cleared when the job finishes. Passwords use Argon2id, sessions and invitations are stored only as hashes, mutating account requests require CSRF validation, and account/API responses are not browser-cached.

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

# DeepSeek
export OMAKASE_API_KEY=your-key
omakase recommend -u your-handle --llm-type deepseek --mode pro
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
| `omakase account-bootstrap` | Import an Argon2id hash for the Lite owner |

Run `omakase recommend --help` for all options. See [CONTRIBUTING.md](CONTRIBUTING.md) for development and extension guidance.

## Hosted deployment

The base Compose stack persists Lite state in the `omakase_lite_data` volume and works without a notification service. Production can add redacted Discord access-request alerts with the secret overlay:

```bash
mkdir -p secrets
# Write the webhook to secrets/access-discord-webhook without committing it.
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

The Discord message contains only the request number, display name, and owner-inbox URL. Email, contact details, and notes stay in the Lite database. Bootstrap the owner by piping an existing Argon2id hash over standard input; the command never prints the hash:

```bash
docker compose exec -T omakase \
  omakase account-bootstrap --email owner@example.com --password-hash-stdin \
  < /secure/path/owner.argon2
```

## Scope

This public repository is the bring-your-own-key recommendation tool plus its recommendation-only Lite accounts. Omakase Plus is a separate private system and is not connected to the hosted demo.

## License

MIT. See [LICENSE](LICENSE).
