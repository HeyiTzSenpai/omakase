PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE TABLE account_provider_keys_v2 (
    user_id INTEGER NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL
        CHECK (
            provider IN (
                'openai',
                'openwebui',
                'anthropic',
                'gemini',
                'deepseek',
                'openrouter'
            )
        ),
    encrypted_key TEXT NOT NULL,
    key_hint TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, provider)
);

INSERT INTO account_provider_keys_v2
    (user_id, provider, encrypted_key, key_hint, created_at, updated_at)
SELECT user_id, provider, encrypted_key, key_hint, created_at, updated_at
  FROM account_provider_keys;

DROP TABLE account_provider_keys;
ALTER TABLE account_provider_keys_v2 RENAME TO account_provider_keys;

ALTER TABLE account_profiles
    ADD COLUMN last_llm_url TEXT NOT NULL DEFAULT '';
ALTER TABLE account_profiles
    ADD COLUMN last_model TEXT NOT NULL DEFAULT '';

COMMIT;

PRAGMA foreign_keys = ON;
