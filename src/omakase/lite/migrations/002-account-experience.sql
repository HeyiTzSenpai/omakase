CREATE TABLE account_provider_keys (
    user_id INTEGER NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL
        CHECK (provider IN ('openai', 'anthropic', 'gemini', 'deepseek', 'openrouter')),
    encrypted_key TEXT NOT NULL,
    key_hint TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, provider)
);

ALTER TABLE account_profiles
    ADD COLUMN last_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE account_profiles
    ADD COLUMN last_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE account_profiles
    ADD COLUMN last_source TEXT NOT NULL DEFAULT '';
ALTER TABLE account_profiles
    ADD COLUMN last_source_username TEXT NOT NULL DEFAULT '';
ALTER TABLE account_profiles
    ADD COLUMN last_use_planning INTEGER NOT NULL DEFAULT 0
        CHECK (last_use_planning IN (0, 1));
ALTER TABLE account_profiles
    ADD COLUMN last_skip_profile INTEGER NOT NULL DEFAULT 0
        CHECK (last_skip_profile IN (0, 1));

ALTER TABLE account_recommendations
    ADD COLUMN watched_score INTEGER
        CHECK (watched_score IS NULL OR watched_score BETWEEN 1 AND 10);
