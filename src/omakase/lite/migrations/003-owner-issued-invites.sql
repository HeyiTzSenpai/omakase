ALTER TABLE account_access_requests
    ADD COLUMN public_number INTEGER;

UPDATE account_access_requests AS current_request
   SET public_number = (
       SELECT COUNT(*)
         FROM account_access_requests AS earlier_request
        WHERE earlier_request.id <= current_request.id
   );

CREATE UNIQUE INDEX account_access_requests_public_number_idx
    ON account_access_requests(public_number);

CREATE TABLE account_request_number_sequence (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    next_number INTEGER NOT NULL CHECK (next_number >= 1)
);

INSERT INTO account_request_number_sequence (singleton, next_number)
SELECT 1, COALESCE(MAX(public_number), 0) + 1
  FROM account_access_requests;

CREATE TABLE account_invites_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    access_request_id INTEGER
        REFERENCES account_access_requests(id) ON DELETE CASCADE,
    email TEXT,
    kind TEXT NOT NULL DEFAULT 'request'
        CHECK (kind IN ('request', 'direct')),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    claimed_at TEXT,
    created_by INTEGER NOT NULL REFERENCES account_users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (kind = 'request' AND access_request_id IS NOT NULL AND email IS NOT NULL)
        OR
        (kind = 'direct' AND access_request_id IS NULL AND email IS NULL)
    )
);

INSERT INTO account_invites_v2
    (id, access_request_id, email, kind, token_hash, expires_at, claimed_at,
     created_by, created_at)
SELECT id, access_request_id, email, 'request', token_hash, expires_at,
       claimed_at, created_by, created_at
  FROM account_invites;

DROP TABLE account_invites;
ALTER TABLE account_invites_v2 RENAME TO account_invites;
