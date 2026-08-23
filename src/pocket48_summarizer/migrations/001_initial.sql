CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    live_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT NOT NULL DEFAULT '',
    member_id TEXT,
    member_name TEXT,
    title TEXT,
    cover_url TEXT,
    replay_started_at TEXT,
    duration_ms INTEGER,
    media_url TEXT,
    danmaku_url TEXT,
    danmaku_loaded_at TEXT,
    audio_path TEXT,
    audio_extracted_at TEXT,
    oss_object_key TEXT,
    oss_uploaded_at TEXT,
    dashscope_task_id TEXT,
    dashscope_task_status TEXT,
    asr_raw_json TEXT,
    asr_completed_at TEXT,
    summary_json TEXT,
    summary_markdown TEXT,
    error_code TEXT,
    error_message TEXT,
    error_retryable INTEGER NOT NULL DEFAULT 0,
    cleanup_warning TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    CHECK (progress_percent BETWEEN 0 AND 100)
);

CREATE INDEX jobs_status_created_idx ON jobs (status, created_at);
CREATE INDEX jobs_lease_idx ON jobs (status, lease_expires_at);

CREATE TABLE transcript_segments (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    speaker_id TEXT,
    text TEXT NOT NULL,
    PRIMARY KEY (job_id, sequence)
);

CREATE INDEX transcript_job_start_idx
    ON transcript_segments (job_id, start_ms);

CREATE TABLE danmaku_entries (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (job_id, sequence)
);

CREATE INDEX danmaku_job_time_idx
    ON danmaku_entries (job_id, timestamp_ms);

CREATE TABLE danmaku_peaks (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    score REAL NOT NULL,
    samples_json TEXT NOT NULL,
    PRIMARY KEY (job_id, rank)
);

CREATE TABLE summary_chunks (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, chunk_index, prompt_version)
);

CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX job_events_job_created_idx
    ON job_events (job_id, created_at DESC);

CREATE TRIGGER job_events_append_only_update
BEFORE UPDATE ON job_events
BEGIN
    SELECT RAISE(ABORT, 'job_events is append-only');
END;

CREATE TRIGGER job_events_append_only_delete
BEFORE DELETE ON job_events
BEGIN
    SELECT RAISE(ABORT, 'job_events is append-only');
END;
