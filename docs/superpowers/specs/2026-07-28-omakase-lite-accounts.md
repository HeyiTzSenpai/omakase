# Omakase Lite accounts and deep recommendation jobs

## Product boundary

Omakase Lite gives approved people a small, persistent recommendation experience on the public BYOK counter. It deliberately excludes every private Plus media-management capability: no Plex access, downloads, acquisition, library automation, or shared Plus database.

Guests keep the original request-local behavior. Signed-in people may save taste notes, completed recommendation menus, and feedback. Provider keys and uploaded MAL exports are never persisted.

## User flow

1. A visitor requests access with email, display name, optional contact, and note.
2. The request is saved in the private Lite database. Discord receives only the request ID, display name, and owner-inbox URL.
3. The owner signs in, approves the request, and copies a one-time invitation.
4. The invitee claims the link within seven days and creates an Argon2id-backed password.
5. Completed menus appear in My Counter. Each recommendation supports Not Interested, Add to My List, and Already Watched.
6. Later menus append recent feedback to the saved taste note so repeated unwanted picks can be avoided.

## Runtime design

- FastAPI serves account pages and JSON endpoints.
- SQLite uses WAL mode and a named Docker volume separate from Omakase Plus.
- Provider work runs in a three-worker in-memory executor. A random job capability is polled for up to one hour after completion.
- Only status, safe errors, and completed recommendations enter the job registry. The provider key and uploaded list exist only in the worker closure and are cleared in `finally`.
- A process restart may interrupt in-flight menus; it cannot expose or replay their credentials.
- DeepSeek Deep uses `deepseek-v4-pro` with an 8,192-token output budget.

## Security controls

- Explicit provider selection prevents ambiguous `sk-` keys from being sent to the wrong company.
- Passwords use Argon2id. Session and invitation values are SHA-256 hashes at rest.
- A one-time invitation travels in the URL fragment and is moved into the claim form body, so reverse-proxy and application access logs never receive it.
- Cookies are HttpOnly, Secure in production, SameSite=Lax, and scoped to `/`.
- Signed-in mutations require a matching CSRF token and same-origin request.
- Anonymous request, login, and invite-claim routes are rate-limited in process; the access form also has a honeypot.
- Admin endpoints enforce the `admin` role. Recommendation feedback updates are user-scoped.
- Account and API responses use `Cache-Control: no-store`.
- CSP, frame denial, referrer, MIME-sniffing, permissions, and cross-origin isolation headers are set by the app.
- The Discord webhook is a Docker secret in the production overlay and is never included in application responses or logs.

## Persistence and recovery

Schema changes are numbered SQL migrations tracked in `account_migrations`. The `omakase_lite_data` volume is the only persistent Lite runtime state. Deployment must back up that volume before replacing the container; rollback restores both the prior image and the compatible volume backup.
