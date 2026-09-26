CREATE TABLE replay_watch_state (
    member_id INTEGER PRIMARY KEY,
    enabled_at_ms INTEGER NOT NULL,
    last_attempt_at_ms INTEGER,
    last_success_at_ms INTEGER,
    last_error_code TEXT
);
