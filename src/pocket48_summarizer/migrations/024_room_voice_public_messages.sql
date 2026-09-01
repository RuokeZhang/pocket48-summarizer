ALTER TABLE room_voice_processing_jobs
ADD COLUMN member_id TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN room_id TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN capture_started_at TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN capture_ended_at TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN messages_status TEXT NOT NULL DEFAULT 'queued'
CHECK (messages_status IN ('queued', 'running', 'completed', 'failed'));

ALTER TABLE room_voice_processing_jobs
ADD COLUMN messages_error_code TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN messages_error_message TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN messages_error_retryable INTEGER NOT NULL DEFAULT 0;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN messages_worker_id TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN messages_lease_expires_at TEXT;

ALTER TABLE room_voice_processing_jobs
ADD COLUMN messages_completed_at TEXT;

CREATE INDEX room_voice_messages_status_created_idx
    ON room_voice_processing_jobs (messages_status, created_at);

CREATE INDEX room_voice_messages_lease_idx
    ON room_voice_processing_jobs (
        messages_status,
        messages_lease_expires_at
    );

CREATE TABLE room_voice_public_messages (
    session_id TEXT NOT NULL
        REFERENCES room_voice_processing_jobs(session_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    nickname TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (session_id, message_id),
    CHECK (timestamp_ms >= 0),
    CHECK (length(nickname) BETWEEN 1 AND 100),
    CHECK (length(text) BETWEEN 1 AND 1000)
);

CREATE INDEX room_voice_public_messages_time_idx
    ON room_voice_public_messages (session_id, timestamp_ms, message_id);
