CREATE TABLE subtitle_translation_requests (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    worker_id TEXT,
    lease_expires_at TEXT,
    requested_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    PRIMARY KEY (job_id, language),
    CHECK (language = 'en'),
    CHECK (status IN ('queued', 'running', 'completed', 'failed'))
);

CREATE INDEX subtitle_translation_status_idx
    ON subtitle_translation_requests (status, requested_at);

CREATE INDEX subtitle_translation_lease_idx
    ON subtitle_translation_requests (status, lease_expires_at);

CREATE TABLE transcript_translations (
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    language TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, sequence, language),
    FOREIGN KEY (job_id, sequence)
        REFERENCES transcript_segments(job_id, sequence)
        ON DELETE CASCADE,
    CHECK (language = 'en')
);

CREATE INDEX transcript_translation_job_idx
    ON transcript_translations (job_id, language, sequence);
