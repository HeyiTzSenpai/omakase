# Omakase owner-invitation-only access

## Product boundary

Lite accounts are for people the owner already knows. Public visitors can use
the request-local BYOK recommendation counter, but they cannot apply for an
account or add themselves to an approval queue.

The authenticated owner opens the private Invitations page, creates a
seven-day one-time link, and chooses how to share it. The recipient opens the
link and supplies their own display name, email, and password. There is no
automatic email or message delivery.

## Security contract

- Owner invitation creation requires an authenticated admin session,
  same-origin validation, CSRF, and a bounded rate limit.
- The database stores only the SHA-256 invitation-token hash. The raw token is
  returned once in the URL fragment and never appears in an HTTP route.
- Account creation and invitation consumption share one immediate SQLite write
  transaction, preventing concurrent double claims with different emails.
- Invitations expire after seven days and work once.
- Passwords remain Argon2id hashes; the resulting account is an ordinary Lite
  member with no Plus, Plex, download, acquisition, or media access.
- The public `/account/request` GET and POST routes, request form, entry links,
  Discord notification code, and production webhook mount are absent.

## Compatibility and numbering

The migration preserves existing request-bound invitations and access history.
It introduces stable public request numbers that are separate from SQLite row
IDs. Retained rows are backfilled in chronological order, so the single
existing real request becomes public request `#1`; future numbers are allocated
from a durable non-reusing sequence.
