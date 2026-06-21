# Omakase Plus Recommendation Intelligence Design

Date: 2026-06-21
Project: Omakase Plus
Branch/context: `plus-mvp`

## Purpose

Omakase Plus should become better at recommending anime the user will actually want to watch, while keeping the dashboard easy to act on. "Up to date" does not mean current-season-only. It means the recommender understands franchise continuity: if the user loved an anime and a sensible new season, movie, OVA, or special exists, that continuation should be considered strongly. If the user dropped, paused, or disliked the franchise, related entries should be blocked by default.

This pass focuses on recommendation intelligence plus the obvious dashboard usability fixes needed to trust and act on picks.

## Locked User Decisions

- Rated `8-10`: boost sensible unwatched continuations.
- Rated `6-7`: allow related entries but do not boost them.
- Rated `1-5`, `DROPPED`, or `PAUSED`: block sequels, spin-offs, side stories, movies, OVAs, and specials by default.
- Prefer the next missing entry in a franchise sequence.
- Later entries may be recommended out of order only when the relation is loose-order, anthology-like, or otherwise does not require prior seasons.
- Airing shows are allowed but clearly labeled. Completed shows are preferred unless the airing show is tied to a franchise the user loved, or has enough released episodes to be worth starting.
- The dashboard should have recommendation lanes: `Best Match`, `New Seasons`, `Hidden Gems`, and `Plan List`.
- Recommendation titles/cards should open the source used for the run: AniList for AniList runs, MyAnimeList for MAL runs.
- `Plan` and `Download` must be separate actions.
- Add local feedback buttons: `Interested`, `Not for me`, `Wrong sequel`, and `Already watched`.
- Disliked-franchise blocks are default behavior, but the LLM may surface a rare exception only when it explicitly explains why the later entry is structurally different and safe to ignore the earlier dislike.

## Current Problems

1. Plus results are not clickable in the dashboard even though the engine already resolves recommendation URLs.
2. `+ Plan & Download` combines a low-risk list action with a download action, making it too easy to trigger Real-Debrid/torrent work by accident.
3. The current franchise filter mainly blocks related entries from already watched or dropped history. It does not positively boost loved-franchise continuations.
4. Candidate selection is score/genre targeted, but lacks a first-class lane for new seasons and recent franchise updates.
5. The prompt has no local feedback history, so the model cannot learn from "wrong pick" or "not for me" without the user editing the taste profile manually.
6. Airing status is not visible, so the user cannot tell whether a pick is complete or ongoing.

## Recommendation Model

### Candidate Metadata

Extend AniList candidate and history fetches with:

- `season`
- `seasonYear`
- `status`
- `startDate`
- `episodes`
- `nextAiringEpisode { episode, airingAt }`
- `relations.edges { relationType, node { id, type, format, status, episodes, season, seasonYear, title } }`

Store this in `MediaItem` fields or a small nested relation structure. MAL can keep a thinner model and use source URLs plus existing metadata until a richer MAL pass is justified.

### Franchise Policy

Build a `FranchisePolicy` per candidate:

- `blocked`: candidate is related to a low-rated, dropped, or paused history item.
- `boosted`: candidate is related to a loved `8-10` history item.
- `neutral`: candidate is related only to `6-7` history or unrelated.
- `sequence_warning`: candidate appears to skip a required earlier entry.
- `loose_order`: candidate relation type or format indicates it can stand alone.

The system should not rely only on title stems. AniList relation edges are primary; title stems remain a fallback for sparse metadata.

### Sequencing

When a loved franchise has multiple unwatched related entries:

1. Prefer the earliest missing required entry.
2. Allow a later entry only when the relation is loose-order, anthology-like, a side story/special that does not require prior continuity, or when AniList metadata provides no clear required path.
3. If sequencing is unclear, label the result as requiring review rather than pretending confidence.

### Airing Preference

Use a simple policy:

- Completed entries are generally preferred.
- Airing entries are allowed and labeled `Airing`.
- Airing loved-franchise continuations may be boosted.
- Airing unrelated entries should rank lower unless they already have enough released episodes or are exceptionally aligned with the user profile.
- The prompt should tell the LLM to mention airing status in the reasoning when it affects the recommendation.

## Recommendation Lanes

### Best Match

Default lane. Mixes loved-franchise continuations, hidden gems, and strong profile/history matches. It should not overfit to recency.

### New Seasons

Prioritizes boosted loved-franchise continuations and current/recent related entries. Blocks disliked/dropped/paused franchises. Shows airing labels and sequencing warnings.

### Hidden Gems

Prioritizes older or less obvious candidates that match the taste profile and ratings, with no strong recency bias. Avoids popular sequels unless they are the best match.

### Plan List

Uses the user's AniList Planning list as the candidate pool. Because the user already selected these, franchise blocking should warn rather than silently drop unless the candidate is an exact already-watched entry.

## Prompt Changes

The prompt should receive:

- The selected lane.
- Loved franchise continuation candidates.
- Blocked disliked/dropped franchise candidates, summarized as "do not recommend these relations."
- Airing/completed status for candidates.
- Local feedback history.
- Sequencing notes when relevant.

The model should still output strict JSON. Add optional fields:

- `anilist_id` or source id, if available.
- `airing_status`
- `franchise_note`
- `lane_reason`

Parsing must remain backwards-compatible with older outputs.

## Feedback Storage

Add a local Plus table, for example `recommendation_feedback`:

- `id`
- `user_id`
- `source`
- `media_id`
- `title`
- `feedback_type` (`interested`, `not_for_me`, `wrong_sequel`, `already_watched`)
- `run_id`
- `created_at`

Behavior:

- `Interested`: slight future boost.
- `Not for me`: future downrank or prompt warning.
- `Wrong sequel`: block or warn on that relation path.
- `Already watched`: exclude exact media id and surface that the user's list may need syncing.

## Dashboard Changes

1. Add a compact segmented lane control near the Run form:
   - `Best Match`
   - `New Seasons`
   - `Hidden Gems`
   - `Plan List`
2. Make the recommendation title or card open the resolved source URL.
3. Split actions:
   - `Plan`
   - `Download`
4. Show status chips where known:
   - `Airing`
   - `Finished`
   - `Loved franchise`
   - `Sequencing check`
5. Add feedback controls on each card:
   - `Interested`
   - `Not for me`
   - `Wrong sequel`
   - `Already watched`
6. Keep the UI server-rendered and consistent with the existing Plus style. This is a functional polish pass, not a full visual redesign.

## Data Flow

1. User selects a lane and runs recommendations.
2. Adapter fetches user history plus richer candidate/relation metadata.
3. Candidate policy marks each candidate as blocked, boosted, neutral, airing, completed, or sequencing-sensitive.
4. Lane selector filters/sorts the pool.
5. Prompt includes the lane, policy notes, profile, ratings, candidates, and feedback.
6. LLM returns picks.
7. Engine resolves source URLs and returns enriched recommendation data.
8. Dashboard renders clickable cards with separate Plan, Download, and feedback actions.
9. Feedback persists and informs future runs.

## Error Handling

- If relation metadata is missing, fall back to title-stem checks and label sequencing as uncertain.
- If a URL cannot be resolved, use the source-specific search URL.
- If feedback save fails, keep the recommendation result visible and show a small error.
- If Download fails, do not undo Plan.
- If Plan fails, Download should remain available only as an explicit user action, not as a chained fallback.

## Testing

Unit tests:

- Loved `8-10` franchise continuation is boosted.
- Dropped/paused/low-rated franchise continuation is blocked.
- `6-7` franchise relation is allowed but not boosted.
- Next-missing-entry logic prefers the earliest required unwatched relation.
- Loose-order relation may allow a later entry.
- Airing loved-franchise continuation is allowed and labeled.
- Unrelated airing entry is lower priority than completed equivalents.
- Recommendation URL survives through Plus dashboard rendering.
- Plan and Download routes operate separately.
- Feedback rows persist and affect future prompt/candidate policy.

Browser verification:

- Dashboard lane control is usable on desktop and mobile.
- Recommendation title/card opens the correct source in a new tab.
- Plan does not trigger Download.
- Download can be triggered separately.
- Feedback buttons update state without breaking the run display.
- Airing and sequencing labels are visible and do not overlap content.

Real-target verification:

- Authenticated `https://anime.jhinx.dev/plus` smoke with the user's account.
- One DeepSeek Pro or Fast run in `Best Match`.
- One `New Seasons` run that proves loved-franchise boosting and disliked-franchise blocking.
- At least one clickable card opens AniList for an AniList run.
- Plan and Download are independently tested on a safe recommendation.

## Out Of Scope

- Full visual redesign of Omakase Plus.
- Public `omakase.jhinx.dev` cutover or Phase 7 public deploy.
- Changing LLM provider billing or subsidizing inference.
- Replacing the current server-rendered FastAPI/Jinja dashboard with an SPA.
- Deep MAL parity for franchise relation metadata.

## Self-Review

- No unresolved product questions remain from the user's answers.
- Scope is focused on recommendation intelligence and dashboard usability, not a full redesign.
- The design preserves the existing Plus branch and private dashboard constraints.
- The testing section includes real browser and real-target gates, not only unit tests.
