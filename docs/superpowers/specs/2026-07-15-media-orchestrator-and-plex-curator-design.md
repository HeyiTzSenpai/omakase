# Omakase Media Orchestrator and Plex Curator Design

## Intent

Turn private Omakase Plus into the durable anime acquisition and library-control
surface for the homelab. A title request must resolve to the intended AniList
entry, acquire complete currently available episode coverage through
Real-Debrid, stay current while airing, and appear in the correct Plex library
with stable season and episode naming.

The first production proof covers the user's requested titles:

- My Hero Academia Seasons 4 through Final Season
- Mushoku Tensei: Jobless Reincarnation Season 3
- Daily Lives of High School Boys
- My Ribdiculous Reincarnation
- Heavy Object
- KAMUI ---He's behind you
- Invincible Seasons 1 through 4 as non-anime TV

## Product Acceptance

A requested title is complete only when:

1. Omakase has resolved the intended AniList or TMDB identity.
2. Every episode aired as of the verification time is represented by an
   accepted Real-Debrid asset.
3. Plex exposes the expected seasons and episode count in the correct library.
4. At least one representative episode per title returns media bytes through a
   fresh Plex playback-range request.
5. A releasing AniList title has an active monitor with a successful scheduled
   execution receipt.

An episode may remain unavailable only after all sensible title aliases and
quality-eligible candidates have been attempted. The retained evidence must
show whether each candidate was absent, mismatched, provider-blocked, unseeded,
or stalled.

## Chosen Architecture

Omakase owns anime identity, candidate selection, episode coverage, and airing
state. A separate Plex curator on CT 111 owns read-only zurg inspection,
normalized symlink overlays, Plex library topology, collections, scans, and
playback proof.

Riven remains the acquisition and naming path for non-anime TV and movies.
Invincible stays in that path. Omakase does not replace Riven or duplicate its
non-anime responsibilities.

### Alternatives Rejected

- **Riven for all media:** it has broad TV coverage but loses the current
  anime-specific AniList identity, Nyaa quality ranking, and Anime-library
  placement guarantees.
- **Host-only cron scripts around the existing downloader:** faster initially,
  but they would preserve the one-torrent/one-row model, provide poor
  observability, and make recovery depend on undocumented runtime state.

## Omakase Acquisition Model

### Identity

Direct requests accept a title, AniList URL/ID, and optional season or arc hint.
Resolution returns:

- AniList ID and canonical English/Romaji/native titles
- format and release status
- total episode count
- `nextAiringEpisode` data when present
- intended Plex title and season number
- genres used by the Plex collection classifier

Ambiguous title searches return a short choice set in the UI. Exact AniList
URLs/IDs and unambiguous searches continue without a confirmation step.

### Candidate Search and Ranking

All English, Romaji, native, season, cour, part, and final-season aliases are
searched before selection. Results are merged and globally deduplicated by
torrent hash; the first alias with a result no longer wins automatically.

Quality policy:

1. Prefer complete 1080p batches.
2. Then prefer complete coverage assembled from 1080p episode or episode-range
   releases.
3. Use 720p only after usable 1080p candidates are exhausted.
4. Prefer HEVC/x265, known quality groups, dual audio, and English or multi-sub
   releases.
5. Reject raw/no-sub releases, 480p, cam sources, wrong seasons, hardware
   encodes already rejected by the current profile, and unrelated franchise
   matches.
6. Prefer at least five seeders. A candidate with one or more seeders is allowed
   when it is the best remaining match; a zero-seeder candidate is allowed only
   when Real-Debrid confirms it is already cached and immediately usable.

Provider-blocked candidates do not end an alias search. Omakase tries the next
globally ranked eligible candidate up to a bounded per-run limit and records the
result.

### Episode Coverage

The system models coverage, not merely a selected torrent. It parses:

- season/episode tokens such as `S03E01`
- numeric episode ranges
- batch markers and complete-season markers
- absolute episode numbers when the target season's offset is known
- specials separately from numbered episodes

A batch can satisfy multiple episodes. Without a valid batch, Omakase selects
the best non-overlapping candidate set that covers every currently aired
episode. Assets are deduplicated by torrent hash and normalized coverage, so
scheduled runs do not add the same torrent twice.

For a releasing title, the required count is `nextAiringEpisode.episode - 1`.
For a finished title, it is the declared episode total. A finished title stays
active until coverage is complete, then its monitor closes successfully.

### Persistence

Add migrations for:

- `media_monitors`: user, AniList identity, aliases, format, season, release
  status, expected/aired episode counts, active state, schedule timestamps,
  failure counters, and last result.
- `media_assets`: monitor, torrent hash, Real-Debrid ID, candidate metadata,
  quality, coverage range, progress/status, and timestamps.
- `media_sync_runs`: monitor/run receipts, counts, outcome, and bounded error
  detail.

Existing planning rows and download attempts remain intact. The current
`rd_torrent_id` field becomes a compatibility pointer to the most recent
accepted asset; it is no longer the source of truth for full coverage.

### Scheduling and Recovery

Every direct request whose AniList status is `RELEASING` enrolls automatically.
Every request also runs an immediate coverage reconciliation, including
finished shows without a batch.

A persistent CT 101 systemd timer runs reconciliation every four hours and
after missed schedules at boot. A database lock prevents overlapping runs. A
torrent that makes no progress across two scheduled checks is deleted from the
user's Real-Debrid queue and replaced with the next candidate. Provider blocks,
network failures, and exhausted candidates remain visible and retryable.

The scheduler uses AniList, Nyaa, and Real-Debrid only; it does not spend LLM
tokens.

## Private Plus UI Rework

Keep the existing nocturne visual identity, authentication, recommendations,
feedback, and settings. Rework the dashboard into a mobile-first library
operations surface:

1. **Add Anime / Bulk Add:** one title per line, with optional season hints or
   AniList URLs.
2. **Resolution Review:** only ambiguous entries require a choice.
3. **Library Jobs:** current coverage, expected coverage, quality, source,
   Real-Debrid state, Plex state, next check, and last result.
4. **Airing Monitors:** active/completed/degraded badges with Sync Now and
   bounded failure detail.
5. **Planning and Recommendations:** preserved below the library actions.

Desktop may use two columns; 390px mobile is the source of truth. All controls
must have labels, keyboard focus, accessible status text, reduced-motion-safe
feedback, and no horizontal overflow. Secrets, magnet URLs, tokens, and full
provider responses never render.

## Private Library Manifest

Omakase exposes a LAN/private-host-only JSON manifest containing accepted media
identity and placement facts:

- AniList ID, canonical Plex title, format, season, and genres
- expected and currently aired episode counts
- accepted Real-Debrid filenames/torrent hashes
- normalized episode coverage and quality

CT 111 pulls the manifest with a dedicated machine credential stored in a
root-readable environment file. The endpoint is read-only, rate-limited, and
does not expose Real-Debrid IDs, API keys, magnets, or user secrets.

## Plex Curator

Move the existing runtime-only anime overlay script into versioned deployment
assets in the Omakase repository and extend it into an idempotent curator.

### Overlay Rules

- Build an Anime TV overlay from manifest-backed Omakase TV entries plus
  existing trusted anime folders.
- Build an Anime Movies overlay from manifest-backed AniList `MOVIE` entries.
- Exclude any zurg top-level folder already referenced by Riven's TV or movie
  symlink trees. This keeps Invincible and future non-anime media out of Anime.
- Support explicit include/exclude and title/season overrides for recovery.
- Normalize files to `Show (Year)/Season NN/Show (Year) - SxxEyy.ext`.
- Preserve specials separately.
- Translate absolute numbering when the manifest supplies the intended season
  and episode coverage. My Hero Season 4 therefore becomes S04E01-S04E25 even
  though its source filenames use episodes 64-88.
- Build in a temporary sibling directory, validate coverage and link targets,
  then atomically swap the overlay.

A CT 111 systemd timer runs the curator every 15 minutes and at boot. Successful
changes trigger only the affected Plex library scans.

### Library Topology

After a Plex database/config backup, converge to exactly four visible sections:

| Library | Source |
|---|---|
| Anime | Curated Anime TV overlay |
| Anime Movies | Curated Anime Movies overlay |
| TV Shows | Riven `shows` symlink tree |
| Movies | Riven `movies` symlink tree |

Rename `Riven TV` to `TV Shows`. Remove the duplicate `Real-Debrid` catch-all
section only after the four replacement paths scan successfully. Removing the
section never deletes Real-Debrid data or zurg files.

### Anime Collections

Every show in Anime receives exactly one managed collection tag:

- `Action & Adventure Anime` when Action or Adventure metadata is present.
- `Comedy & Couch Anime` otherwise.

Action/Adventure wins on overlap. A versioned override map handles personal
classification exceptions without changing upstream metadata. The curator
preserves all unrelated collection tags.

## Requested-Title Migration

The first live reconciliation will:

1. Ingest the already accepted My Hero Academia Season 4-Final and Heavy Object
   assets into normalized Anime paths.
2. Search all eligible aliases for a better 1080p My Hero Season 4 source; retain
   the accepted 720p dual-audio batch only if every usable 1080p alternative is
   blocked or unavailable.
3. Replace the stalled Daily Lives asset and obtain 12-episode coverage from a
   batch or episode set.
4. Obtain all 12 My Ribdiculous Reincarnation episodes.
5. Obtain every currently aired Jobless Reincarnation Season 3 and KAMUI episode
   and leave both monitors active.
6. Preserve Invincible Seasons 1-4 in TV Shows, including the existing repaired
   Season 2 symlinks, while bringing them under reproducible Riven/Plex evidence.

## Verification

### Local gate

- Focused red/green tests for resolution, alias merging, episode parsing,
  coverage selection, deduplication, progress recovery, monitor enrollment,
  manifest authorization, overlay normalization, and collection rules.
- Full pytest suite, Ruff check, Ruff format check, and any existing type/build
  gate.
- Dashboard browser proof at desktop and 390px mobile, including accessibility,
  no overflow, and safe error rendering.

### Real-target gate

- Commit and push the exact intended `plus-mvp` head.
- Back up Omakase SQLite, Plex database/config, curator config, and relevant
  systemd units.
- Deploy that exact head to private Plus on CT 101 and versioned curator assets
  to CT 111.
- Run the timers manually once and verify their scheduled/boot state.
- Verify each requested title's current episode count through Plex API.
- Execute representative fresh Plex byte-range playback probes.
- Verify the four library names/paths and two Anime collections in a real Plex
  web session on desktop and mobile where applicable.
- Record unavailable candidates only with the required exhausted-search
  evidence.

## Rollback

- Restore the pre-deploy Omakase database and previous Plus image/source.
- Restore the Plex database/config backup and prior library section definitions.
- Restore the previous overlay script/config and systemd units.
- Atomically swap back the prior overlay directories.
- Real-Debrid media is never deleted as part of Plex topology rollback. Only a
  specifically stalled candidate owned by a monitor may be removed when the
  replacement policy fires.

## Security and Scope Boundaries

- Plus and the manifest remain private; public `omakase.jhinx.dev/plus*` stays
  404.
- No secrets appear in logs, UI, committed files, manifests, or handoff output.
- The design changes private Omakase, CT 101, CT 111, Real-Debrid state owned by
  the user, and Plex metadata. It does not publish repositories, grant outsider
  access, or change public-site behavior.
