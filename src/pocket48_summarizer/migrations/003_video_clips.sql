CREATE TABLE video_clips (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    timeline_index INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    oss_object_key TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (job_id, timeline_index),
    CHECK (timeline_index >= 0),
    CHECK (start_ms >= 0),
    CHECK (end_ms > start_ms),
    CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX video_clips_status_updated_idx
    ON video_clips (status, updated_at);
