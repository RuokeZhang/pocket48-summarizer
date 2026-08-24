CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    username_normalized TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE user_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX user_sessions_expiry_idx
    ON user_sessions (expires_at);

CREATE TABLE job_access (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, user_id)
);

CREATE INDEX job_access_user_created_idx
    ON job_access (user_id, created_at DESC);

INSERT INTO users (
    id, username, username_normalized, password_hash,
    is_admin, is_active, failed_login_count, created_at
) VALUES (
    'local', 'local', 'local', '', 1, 1, 0, datetime('now')
);

INSERT INTO job_access (job_id, user_id, created_at)
SELECT id, 'local', created_at FROM jobs;
