CREATE TABLE IF NOT EXISTS video_clip_exports (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    timeline_index INTEGER NOT NULL,
    timeline_title TEXT NOT NULL DEFAULT '',
    requested_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    request_id TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    subtitle_mode TEXT NOT NULL,
    include_danmaku INTEGER NOT NULL DEFAULT 0,
    render_version TEXT NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    oss_object_key TEXT,
    error_message TEXT,
    warning_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (job_id, request_id),
    CHECK (timeline_index >= 0),
    CHECK (start_ms >= 0),
    CHECK (end_ms > start_ms),
    CHECK (subtitle_mode IN ('off', 'zh', 'en', 'bilingual')),
    CHECK (include_danmaku IN (0, 1)),
    CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS video_clip_exports_job_timeline_created_idx
    ON video_clip_exports (job_id, timeline_index, created_at DESC);

CREATE INDEX IF NOT EXISTS video_clip_exports_status_updated_idx
    ON video_clip_exports (status, updated_at);

INSERT OR IGNORE INTO video_clip_exports (
    id,
    job_id,
    timeline_index,
    timeline_title,
    requested_by_user_id,
    request_id,
    start_ms,
    end_ms,
    subtitle_mode,
    include_danmaku,
    render_version,
    filename,
    status,
    oss_object_key,
    error_message,
    warning_message,
    created_at,
    updated_at,
    completed_at
)
SELECT
    'legacy-' || replace(job_id, '-', '') || '-' || timeline_index,
    job_id,
    timeline_index,
    '',
    NULL,
    'legacy:' || timeline_index,
    start_ms,
    end_ms,
    'off',
    0,
    'legacy-v1',
    filename,
    status,
    oss_object_key,
    error_message,
    NULL,
    created_at,
    updated_at,
    completed_at
FROM video_clips;

CREATE TABLE IF NOT EXISTS clip_boundary_suggestions (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    cache_key TEXT NOT NULL,
    boundary_kind TEXT NOT NULL,
    segment_sequence INTEGER NOT NULL,
    anchor_ms INTEGER NOT NULL,
    suggested_ms INTEGER NOT NULL,
    silence_start_ms INTEGER,
    silence_end_ms INTEGER,
    analysis_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, cache_key),
    CHECK (boundary_kind IN ('start', 'end')),
    CHECK (segment_sequence >= 0),
    CHECK (anchor_ms >= 0),
    CHECK (suggested_ms >= 0),
    CHECK (
        silence_start_ms IS NULL
        OR silence_start_ms >= 0
    ),
    CHECK (
        silence_end_ms IS NULL
        OR silence_end_ms >= 0
    )
);
