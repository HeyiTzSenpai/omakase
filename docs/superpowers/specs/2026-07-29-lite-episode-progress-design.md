# Lite episode-progress design

## Goal

Let a signed-in Omakase Lite member mark a recommendation as currently
watching and record the number of episodes consumed, without conflating that
state with a fully watched and scored title. Save locally first and synchronize
compatible AniList recommendations to the authenticated member's own list.

## User behavior

Each recommendation keeps the existing **Already watched** action and 1–10
score. It gains a separate **Watching** action with a required positive integer
**Episodes watched** value.

- Watching saves progress locally, suppresses the title from later menus, and
  appears in a dedicated **Currently watching** dashboard list.
- Already watched remains a completed state with a required score and appears
  under **Watched & rated**.
- Changing to Watching clears the local completed score. Changing to Already
  watched clears local episode progress. Other feedback states clear both.
- Omakase does not guess the series episode total. AniList validation failures,
  including progress beyond an allowed total, leave the local value intact and
  show a retryable failed-sync receipt.

## Storage and migration

Migration 007 adds two nullable columns to `account_recommendations`:

- `watch_status`, constrained to `current` or `completed`;
- `watched_episodes`, constrained to a positive integer when present.

The existing checked `feedback_state` column remains unchanged. Both logical
watch states use its existing `watched` value so migration stays additive and
does not rebuild the production table while 24 known foreign-key orphans are
being deliberately preserved. Existing `watched` rows are backfilled to
`watch_status = 'completed'`.

Application writes enforce these invariants:

- current: `feedback_state = 'watched'`, `watch_status = 'current'`,
  positive `watched_episodes`, null `watched_score`;
- completed: `feedback_state = 'watched'`, `watch_status = 'completed'`,
  null `watched_episodes`, score from 1 through 10;
- every other feedback state: null watch status, episodes, score, and tracker
  receipt fields.

Changing either watch state resets the tracker receipt before attempting the
new synchronization.

## AniList synchronization

The existing authorization, encrypted token, authenticated Viewer,
source-username match, exact media-ID parsing, and returned-receipt validation
remain mandatory.

Watching sends `SaveMediaListEntry` with the AniList media ID,
`status: CURRENT`, and `progress`. It requests and validates the returned entry
ID, media ID, status, and progress. It does not send a score, so an unrelated
remote score is not intentionally changed.

Already watched continues to send `status: COMPLETED` and `scoreRaw`.
Connection backfill and retry queries include both compatible current-progress
and completed-score rows. Rows from MyAnimeList or a mismatched AniList source
remain local and receive a truthful unavailable or account-mismatch receipt.

AniList documents `CURRENT` as currently watching, `progress` as episodes
consumed, and both fields as supported `SaveMediaListEntry` arguments:

- <https://docs.anilist.co/reference/enum/medialiststatus>
- <https://docs.anilist.co/reference/mutation>

## API and interface

The feedback endpoint accepts one new logical payload:

```json
{"state": "watching", "watched_episodes": 3}
```

It rejects booleans, missing values, zero, negative values, fractions, and
non-numeric values with an actionable message. The success response returns
`state`, `watched_episodes`, and the tracker-sync receipt. Existing payloads
remain backward compatible.

The recommendation-card controls present Watching/episode progress separately
from Already watched/score. Confirmation text states whether progress is saved
only in Omakase, awaiting connection, failed, or confirmed by AniList.

The account dashboard renders **Currently watching** above **Watched & rated**.
Each current title shows its episode count and tracker receipt. Feedback context
uses `Currently watching: Title (3 episodes).` so future menu generation avoids
recommending it again while retaining useful taste context.

## Failure and privacy behavior

Local persistence succeeds even when AniList is disconnected or unavailable.
Remote failure never rolls back the member's local progress. Tokens, OAuth
state, and plaintext provider credentials are never returned or logged.
Cross-account writes continue to fail closed.

## Verification

Implementation follows red-green-refactor:

1. migration preservation and current/completed invariants;
2. AniList `CURRENT` plus progress request and receipt validation;
3. route validation, local-only behavior, matching-account synchronization,
   mismatch rejection, and connection backfill;
4. JavaScript payload/confirmation behavior and rendered controls/lists;
5. full Python/JavaScript, Ruff, package, Compose, and CI gates;
6. authenticated desktop/mobile browser proof, live consent-safe behavior,
   production migration/data preservation, exact source/live convergence, and
   rollback.

No real user's AniList progress is changed for deployment proof without that
user explicitly submitting the new action.
