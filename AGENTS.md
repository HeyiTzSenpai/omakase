# omakase — project instructions for AI agents

LLM-powered anime sommelier: a Python CLI + FastAPI web app. Applies to Claude Code, Codex, and Cherry Claw (DeepSeek). Canonical narrative state: `C:\Users\qazws\Nextcloud2\Homelab Vault\Projects\omakase\README.md`. Active brief: `C:\Users\qazws\Nextcloud2\Homelab Vault\Agent-Sessions\omakase\01-plus-mvp-me-only.md`.

## Stack & layout

- Python (FastAPI web + Click-style CLI). Package entry: `omakase = omakase.cli:cli`.
- Local **`.venv` already exists** in the repo root — use it, do not create a new one.
- SQLite (WAL, per-request connections). Plus data at `data/plus.db`.
- Current working branch: **`plus-mvp`** (the me-only multi-tenant Plus MVP). The old `plus-waitlist` branch is superseded.

## Run & verify (PowerShell)

```powershell
# from repo root
.\.venv\Scripts\python.exe -m pytest            # test suite (baseline ~154 passing)
.\.venv\Scripts\python.exe -m omakase.cli --help
.\.venv\Scripts\python.exe -m omakase.cli recommend -u <anilist_user>
```
- Web UI port: `OMAKASE_PORT` (default **8765**). Plus backend runs on **8766** when deployed.
- LLM is **BYOK** via `OMAKASE_API_KEY` (OpenAI / Anthropic / local Ollama). Do not hardcode keys.
- Plus secrets (encryption + OAuth): `OMAKASE_PLUS_MASTER_KEY`, `OMAKASE_PLUS_ADMIN_TOKEN`, `ANILIST_CLIENT_ID/SECRET`, `OVERSEERR_*`. **You do NOT create Vaultwarden entries — surface a checklist for the user.** Missing env vars → write `docs/plus-secrets-checklist.md` and continue with mocks (per the brief's auto-degrade path).

## Deploy (gated)

- Plus deploy recipe: **`DEPLOY-PLUS.md`** in repo root + `compose-plus.yaml`. Target: **CT 101 docker-edge** (optiplex `192.168.50.141`), stack dir `/opt/stacks/omakase-plus/`, port 8766, **LAN-only (no NPM proxy)**.
- Public site (`omakase.jhinx.dev`) deploys with a **jhinx-specific overlay** that lives OFF-repo at `/opt/stacks/omakase-overlay/`; its `apply.sh` must run **after** rsync and **before** `docker compose up -d --build`.
- 🛑 **HARD HALT:** the public-site refactor (drop waitlist → "try with your key" CTA) is the only thing that ships to a public surface and requires **explicit user approval** (Phase 7 of the brief). Private `plus-mvp` branch pushes + local Plus deploy are fine without asking; the public cutover is not. Do not rationalize your way past this gate.

## Guardrails

- Plus is **local-only / me-only** behind `OMAKASE_PLUS_PRIVATE=true`. It must not spend LLM/API tokens from the public surface; the public site is waitlist-removed BYOK demo only.
- Verify the web UI in a real browser before claiming a UI change works. **Cherry Claw (DeepSeek) has no browser tool** — run any headless checks you can, then hand the visual "does it look right" proof to the user or a Claude Code session.
- Aesthetic carryover: keep the current public palette/typography unless the user asks for a redesign.
- Sync `Homelab Vault\Projects\omakase\README.md` + memory in the same batch when project state changes.
