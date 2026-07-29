CREATE TABLE account_oauth_flows (
    state_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider = 'anilist'),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_account_oauth_flows_user_provider
    ON account_oauth_flows(user_id, provider);

CREATE TABLE account_anilist_connections (
    user_id INTEGER PRIMARY KEY REFERENCES account_users(id) ON DELETE CASCADE,
    anilist_user_id INTEGER NOT NULL CHECK (anilist_user_id > 0),
    anilist_username TEXT NOT NULL,
    encrypted_access_token TEXT NOT NULL,
    connected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE account_recommendations
    ADD COLUMN tracker_sync_state TEXT NOT NULL DEFAULT 'local_only'
        CHECK (tracker_sync_state IN (
            'local_only',
            'connection_required',
            'account_mismatch',
            'unavailable',
            'synced',
            'failed'
        ));

ALTER TABLE account_recommendations
    ADD COLUMN tracker_sync_detail TEXT;

ALTER TABLE account_recommendations
    ADD COLUMN tracker_remote_entry_id INTEGER;

ALTER TABLE account_recommendations
    ADD COLUMN tracker_synced_at TEXT;
