CREATE TABLE account_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE account_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES account_users(id) ON DELETE CASCADE,
    taste_profile TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE account_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX account_sessions_user_idx ON account_sessions(user_id);

CREATE TABLE account_access_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'declined', 'claimed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notified_at TEXT,
    decided_at TEXT,
    decided_by INTEGER REFERENCES account_users(id)
);

CREATE TABLE account_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    access_request_id INTEGER NOT NULL
        REFERENCES account_access_requests(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    claimed_at TEXT,
    created_by INTEGER NOT NULL REFERENCES account_users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE account_recommendation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_username TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX account_runs_user_idx ON account_recommendation_runs(user_id, id DESC);

CREATE TABLE account_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES account_recommendation_runs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    predicted_score REAL NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    best_match_from_history TEXT NOT NULL DEFAULT '',
    url TEXT,
    source TEXT,
    feedback_state TEXT NOT NULL DEFAULT 'neutral'
        CHECK (feedback_state IN ('neutral', 'not_interested', 'saved', 'watched')),
    feedback_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX account_recommendations_user_idx
    ON account_recommendations(user_id, feedback_state, id DESC);
