# Plus Secrets Checklist

> Generated 2026-05-26 by Phase 0 of the `plus-mvp` brief.
> Codex does NOT touch Vaultwarden directly. The user creates these entries.

## Vaultwarden entries to create

| Entry name | Field | Maps to env var | Notes |
|---|---|---|---|
| `omakase-plus-master` | `master_key` | `OMAKASE_PLUS_MASTER_KEY` | 32-byte random hex for AES-256-GCM encryption at rest. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `omakase-plus-seed` | `email` | `OMAKASE_SEED_EMAIL` | Your login email for the seed account |
| `omakase-plus-seed` | `password` | `OMAKASE_SEED_PASSWORD` | Strong password for the seed account |
| `omakase-plus-anilist` | `client_id` | `ANILIST_CLIENT_ID` | AniList OAuth app — see `docs/anilist-oauth-setup.md` |
| `omakase-plus-anilist` | `client_secret` | `ANILIST_CLIENT_SECRET` | AniList OAuth app secret |
| `omakase-plus-overseerr` | `url` | `OVERSEERR_URL` | Default: `http://overseerr.lab` or LAN IP like `http://192.168.50.XXX:5055` |
| `omakase-plus-overseerr` | `api_key` | `OVERSEERR_API_KEY` | From Overseerr Settings → API → copy key. Mark with custom field `omakase_plus_link: true` |

## Already exists (keep)

| Env var | Source | Notes |
|---|---|---|
| `OMAKASE_PLUS_ADMIN_TOKEN` | Existing `.env.example` | Used for admin CLI operations |
| `OMAKASE_API_KEY` | Existing | Public demo BYOK key |
| `MAL_CLIENT_ID` | Existing | MAL source support |

## Runtime mode flag

| Env var | Value | Effect |
|---|---|---|
| `OMAKASE_PLUS_PRIVATE` | `true` | Enables Plus routes (`/plus/*`). Set on workstation only. |
| `OMAKASE_PLUS_PRIVATE` | unset or `false` | Plus routes return 404. This is the public CT-101 mode. |

## TODO status

- [ ] `OMAKASE_PLUS_MASTER_KEY` — TODO: user must set
- [ ] `OMAKASE_SEED_EMAIL` — TODO: user must set
- [ ] `OMAKASE_SEED_PASSWORD` — TODO: user must set
- [ ] `ANILIST_CLIENT_ID` — TODO: user must set (see anilist-oauth-setup.md)
- [ ] `ANILIST_CLIENT_SECRET` — TODO: user must set
- [ ] `OVERSEERR_URL` — TODO: user must set
- [ ] `OVERSEERR_API_KEY` — TODO: user must set

> Codex proceeds with mocks for missing env vars per auto-degrade. Leftover TODOs surface in the Phase 7 ask.
