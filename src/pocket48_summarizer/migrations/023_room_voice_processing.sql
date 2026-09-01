CREATE TABLE room_voice_processing_jobs (
    session_id TEXT PRIMARY KEY,
    monitor_id TEXT NOT NULL,
    member_name TEXT,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT NOT NULL DEFAULT '',
    segment_count INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    audio_path TEXT,
    oss_object_key TEXT,
    dashscope_task_id TEXT,
    dashscope_task_status TEXT,
    asr_raw_json TEXT,
    summary_json TEXT,
    summary_markdown TEXT,
    error_code TEXT,
    error_message TEXT,
    error_retryable INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    CHECK (
        stage IN (
            'queued',
            'preparing_audio',
            'uploading_audio',
            'transcribing',
            'normalizing_transcript',
            'summarizing_chunks',
            'summarizing_final',
            'cleaning_up',
            'completed'
        )
    ),
    CHECK (progress_percent BETWEEN 0 AND 100),
    CHECK (segment_count > 0),
    CHECK (total_bytes > 0)
);

CREATE INDEX room_voice_processing_status_created_idx
    ON room_voice_processing_jobs (status, created_at);

CREATE INDEX room_voice_processing_lease_idx
    ON room_voice_processing_jobs (status, lease_expires_at);

CREATE TABLE room_voice_transcript_segments (
    session_id TEXT NOT NULL
        REFERENCES room_voice_processing_jobs(session_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    speaker_id TEXT,
    text TEXT NOT NULL,
    PRIMARY KEY (session_id, sequence)
);

CREATE INDEX room_voice_transcript_session_start_idx
    ON room_voice_transcript_segments (session_id, start_ms);

CREATE TABLE room_voice_summary_chunks (
    session_id TEXT NOT NULL
        REFERENCES room_voice_processing_jobs(session_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, chunk_index, prompt_version)
);
