CREATE TABLE annual_report_jobs (
    id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL,
    uid TEXT NOT NULL DEFAULT '',
    member_pocket_id TEXT NOT NULL,
    member_name TEXT NOT NULL,
    scope TEXT NOT NULL,
    extra_nicknames_json TEXT NOT NULL DEFAULT '[]',
    requester_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_text TEXT NOT NULL DEFAULT '',
    result_json TEXT,
    error_code TEXT,
    error_message TEXT,
    worker_id TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    CHECK (scope IN ('2025-2026', '2026')),
    CHECK (status IN ('queued', 'running', 'completed', 'failed'))
);

CREATE INDEX annual_report_jobs_status_idx
    ON annual_report_jobs (status, created_at);

CREATE INDEX annual_report_jobs_cache_idx
    ON annual_report_jobs (cache_key, created_at);

CREATE INDEX annual_report_jobs_requester_idx
    ON annual_report_jobs (requester_hash, created_at);

CREATE TABLE annual_report_live_cache (
    live_id TEXT PRIMARY KEY,
    member_pocket_id TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    lines_blob BLOB,
    fetched_at TEXT NOT NULL,
    CHECK (status IN ('read', 'no_file'))
);

CREATE INDEX annual_report_live_cache_member_idx
    ON annual_report_live_cache (member_pocket_id, started_at_ms);

CREATE TABLE annual_report_member_index (
    pocket_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    team_name TEXT NOT NULL DEFAULT '',
    in_group INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    CHECK (in_group IN (0, 1))
);

CREATE TABLE annual_report_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
