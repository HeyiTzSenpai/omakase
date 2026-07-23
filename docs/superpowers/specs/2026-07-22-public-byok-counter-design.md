# Omakase Public BYOK Counter

## Purpose

The public Omakase site should let an anime fan turn their own scored watch
history into a small, understandable recommendation menu using a cloud model
key they supply for that request. It is a focused public utility, not a preview
of the private Plus automation system.

## Product boundary

- Public: AniList username, MyAnimeList export, optional taste notes, cloud BYOK
  provider selection, recommendation generation, and outbound anime links.
- Self-hosted only: Ollama and LM Studio, because a public server cannot reach a
  visitor's local machine.
- Private only: accounts, acquisition, Real-Debrid, Plex, schedules, monitoring,
  and the user's private taste or library data.
- The hosted request may keep values in memory for the duration of one request.
  It must not write the profile or model key to disk, process-wide environment,
  logs, analytics, cookies, or a database.

## Experience

The first viewport acts like a quiet chef's counter. A visitor sees a concise
promise, a three-part route, and the start of the real form without a marketing
detour. The route is:

1. Choose the model: OpenAI, Anthropic, Gemini, or OpenRouter; Fast or Deep.
2. Bring the history: public AniList username or private MAL export.
3. Add taste notes: optional prose, with a score-only path for a wider menu.

Advanced model URL and exact model name remain available in a collapsed
details panel. The primary action stays visible at the end of the route. Errors
appear beside the part that needs correction and a request can be cancelled.

Results are presented as a tasting menu. Each card includes title, predicted
fit, a short reason, the closest match from history, and a source link. A fresh
run returns the visitor to the route while keeping only browser-held form state.

## Visual system

Direction: nocturnal Japanese listening bar meets an observatory kitchen.

- Near-black plum is the room, warm rice-paper is the reading color, brass is
  the action accent, and ume red is reserved for errors and strong emphasis.
- Newsreader-style display type gives the menu an editorial voice; a compact
  grotesk handles controls; a mono face is limited to privacy receipts and
  technical details.
- A generated still-life hero plate may show a dark counter, small luminous
  dishes, and constellation-like steam. It must not contain text, logos, anime
  characters, or a moving shimmer.
- Thin rules, offset columns, numbered courses, and a persistent preparation
  rail replace nested cards and oversized rounded panels.
- Motion is purposeful: one restrained entrance, a progress line during the
  request, and a staggered result reveal. Reduced motion removes all travel.

## Architecture and data flow

- Keep FastAPI, the server-rendered HTML template, and dependency-free browser
  JavaScript. Split public template behavior into a dedicated static script so
  the HTML remains readable and testable.
- Add an inline taste profile field to `OmakaseConfig` and pass the request key
  directly to `get_llm`. CLI file-based profiles and environment fallback keys
  remain supported.
- Never mutate `os.environ` from a public request. MyAnimeList live API access
  is removed from the hosted form; MAL export stays available and avoids a
  shared Client ID entirely.
- Restrict hosted provider URLs to exact allowlisted HTTPS origins. Custom and
  local URLs are available only in explicitly self-hosted mode so the public
  endpoint cannot be used as an SSRF proxy.
- Add an honest `/api/health` response containing the service and exact source
  commit. Build and deployment must inject that commit.

## Failure handling

- Reject missing cloud keys, missing history input, oversized or invalid MAL
  exports, unsupported provider URLs, and blank required values before work.
- Translate provider authentication, model, rate-limit, timeout, and upstream
  failures into visitor language without including response bodies or secrets.
- Do not return arbitrary exception text from the public endpoint. Unknown
  errors produce a request-safe generic message and a server-side exception log
  with no submitted key or profile.

## Verification

- Unit tests prove per-request key/profile isolation, no environment mutation,
  provider URL allowlisting, private Plus route isolation, public copy, favicon,
  health commit, and friendly error redaction.
- The full existing suite, Ruff, package build, and container build pass.
- Desktop and 390 pixel browser proof clicks provider, source, score-only, form
  validation, and a mocked recommendation result. It checks overflow, keyboard
  focus, console errors, request failures, reduced motion, and serious or
  critical accessibility findings.
- Production deploy captures the previous image as rollback, deploys the exact
  pushed public commit, verifies `/api/health`, then repeats the browser proof
  against `https://omakase.jhinx.dev`.

## Explicitly excluded

No login, waitlist, Plus controls, acquisition request, watch automation,
server-side saved profile, public local-model relay, model billing proxy,
recommendation history, social sharing, or account analytics are part of this
release.
